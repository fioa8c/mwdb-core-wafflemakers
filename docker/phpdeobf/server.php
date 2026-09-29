<?php
/**
 * HTTP sidecar wrapper around the PHPDeobfuscator pipeline.
 *
 *   POST /deobfuscate  {"source": "...", "filename": "x.php", "options": {...}}
 *   GET  /health
 *
 * See docker/plugins/phpdeobf/README.md for the full contract.
 */

require __DIR__ . '/vendor/autoload.php';

use PHPDeobfuscator\Pipeline;
use PHPDeobfuscator\Deobfuscator;
use PHPDeobfuscator\Analysis\ReportFormatter;

ini_set('xdebug.var_display_max_depth', -1);
ini_set('memory_limit', getenv('PHPDEOBF_MEMORY_LIMIT') ?: '512M');
ini_set('xdebug.max_nesting_level', 1000);
// The engine's constant-folder (src/Resolver.php) calls PHP's own
// defined()/constant() on barewords it finds in the sample being analysed
// (e.g. a sample that sets up curl options via CURLOPT_* constants). Some of
// those constants are themselves deprecated in this PHP version, so simply
// *resolving* one during static analysis fires an E_DEPRECATED notice. The
// built-in server's SAPI is "cli-server", not "cli", so display_errors=stderr
// isn't honored there (PHP only special-cases the plain CLI SAPI) — the
// notice is written straight into the HTTP response body ahead of our JSON,
// corrupting it for any client that parses it strictly, and can even trigger
// a cascading "headers already sent" warning. Turn display off and rely on
// log_errors (captured by `docker logs`, via the SAPI's own error log) for
// visibility instead of the response body.
ini_set('display_errors', '0');
ini_set('log_errors', '1');
// Timeout chain: this 60 s budget < the plugin client's 90 s < gunicorn and
// nginx's 120 s, so a slow sample fails here first with a clean error.
set_time_limit(60);

$MAX_INPUT_BYTES = (int) (getenv('MAX_INPUT_BYTES') ?: 5 * 1024 * 1024);
$PEEL_TIMEOUT = (int) (getenv('PHPDEOBF_PEEL_TIMEOUT') ?: 10);
$OPTION_KEYS = ['strip_comments', 'execute_pure', 'eval_peel', 'remove_dead_code', 'rename_vars', 'analyze'];

function engine_version() {
    $f = __DIR__ . '/VERSION';
    if (!is_file($f)) return 'unknown';
    $first = strtok((string) file_get_contents($f), "\n");
    return trim($first) ?: 'unknown';
}

function emit($status, $payload) {
    http_response_code($status);
    header('Content-Type: application/json');
    $json = json_encode($payload, JSON_INVALID_UTF8_SUBSTITUTE);
    if ($json === false) {
        error_log('phpdeobf: response not serialisable: ' . json_last_error_msg());
        http_response_code(200);
        $json = '{"status":"error","code":"internal_error","message":"response not serialisable"}';
    }
    echo $json;
    exit;
}

function emit_error($code, $message) {
    emit(200, ['status' => 'error', 'code' => $code, 'message' => $message]);
}

$path = parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH);
$method = $_SERVER['REQUEST_METHOD'];

if ($method === 'GET' && $path === '/health') {
    emit(200, [
        'status' => 'ok',
        'evalhook_available' => Pipeline::evalHookAvailable(),
        'phpdeobf' => engine_version(),
    ]);
}
if ($method !== 'POST' || $path !== '/deobfuscate') {
    emit(404, ['status' => 'error', 'code' => 'not_found', 'message' => 'unknown route']);
}

$raw = file_get_contents('php://input');
$body = json_decode($raw, true);
if (!is_array($body)) {
    emit(400, ['status' => 'error', 'code' => 'bad_request', 'message' => 'body must be a JSON object']);
}
$source = $body['source'] ?? null;
$filename = $body['filename'] ?? 'input.php';
if (!is_string($source)) {
    emit(400, ['status' => 'error', 'code' => 'bad_request', 'message' => 'source is required and must be a string']);
}
if (!is_string($filename) || $filename === '') {
    $filename = 'input.php';
}
$options = array_fill_keys($OPTION_KEYS, false);
$given = $body['options'] ?? [];
if (!is_array($given)) {
    emit(400, ['status' => 'error', 'code' => 'bad_request', 'message' => 'options must be an object']);
}
foreach ($OPTION_KEYS as $k) {
    if (array_key_exists($k, $given)) {
        if (!is_bool($given[$k])) {
            emit(400, ['status' => 'error', 'code' => 'bad_request', 'message' => "option $k must be a boolean"]);
        }
        $options[$k] = $given[$k];
    }
}
if (strlen($source) > $MAX_INPUT_BYTES) {
    emit_error('input_too_large', sprintf('source is %d bytes; cap is %d', strlen($source), $MAX_INPUT_BYTES));
}

$started = microtime(true);
$warnings = [];
$peeled = null;
$analysis = null;

try {
    list($tree, $output) = Pipeline::deobfuscate(
        $source, $filename, false,
        $options['strip_comments'], $options['execute_pure'], $options['remove_dead_code'], $options['rename_vars']
    );

    if ($options['eval_peel']) {
        if (!Pipeline::evalHookAvailable()) {
            $warnings[] = ['code' => 'eval_peel_unavailable', 'message' => 'eval-hook extension not found in this sidecar'];
        } else {
            $peelError = null;
            $t0 = microtime(true);
            $peeledOut = Pipeline::peelEvalLayers(
                $source, $filename,
                $options['strip_comments'], $options['execute_pure'], $options['remove_dead_code'], $options['rename_vars'],
                $peelError, ['timeout' => $PEEL_TIMEOUT]
            );
            if ($peeledOut !== null) {
                // The header carries the peel's wall-clock time; drop it so the
                // peeled output (and the TextBlob made from it) is deterministic
                // and dedupes across runs. `secs` below keeps the timing.
                $peeledOut = preg_replace('/ captured in [\d.]+s\./', '.', $peeledOut, 1);
                $layers = preg_match_all('/^\/\/ ===== eval\(\) layer \d+/m', $peeledOut);
                $peeled = ['output' => $peeledOut, 'layers' => $layers, 'secs' => round(microtime(true) - $t0, 2)];
            } else {
                $code = 'eval_peel_error';
                if (strpos((string) $peelError, 'no eval() layers') !== false) $code = 'eval_peel_no_layers';
                if (strpos((string) $peelError, 'timed out') !== false) $code = 'eval_peel_timeout';
                $warnings[] = ['code' => $code, 'message' => (string) $peelError];
            }
        }
    }

    if ($options['analyze']) {
        $formatter = new ReportFormatter();

        $findingsStatic = (new Deobfuscator())->analyze($output, $tree);
        $analysis = json_decode($formatter->formatJson($findingsStatic, basename($filename)), true);
        foreach (['sources', 'sinks', 'errors'] as $section) {
            foreach ($analysis[$section] as &$finding) {
                $finding['origin'] = 'static';
            }
            unset($finding);
        }

        if ($peeled !== null) {
            $findingsPeeled = (new Deobfuscator())->analyze($peeled['output'], null);
            $peeledPayload = json_decode($formatter->formatJson($findingsPeeled, basename($filename)), true);
            foreach (['sources', 'sinks', 'errors'] as $section) {
                foreach ($peeledPayload[$section] as &$finding) {
                    $finding['origin'] = 'peeled';
                }
                unset($finding);
            }

            foreach (['sources', 'sinks', 'errors'] as $section) {
                $analysis[$section] = array_merge($analysis[$section], $peeledPayload[$section]);
            }
            $analysis['summary']['source_count'] += $peeledPayload['summary']['source_count'];
            $analysis['summary']['sink_count'] += $peeledPayload['summary']['sink_count'];
            $analysis['summary']['auto_exec_count'] += $peeledPayload['summary']['auto_exec_count'];
            $categories = $analysis['summary']['categories_present'];
            foreach ($peeledPayload['summary']['categories_present'] as $cat) {
                if (!in_array($cat, $categories, true)) {
                    $categories[] = $cat;
                }
            }
            $analysis['summary']['categories_present'] = $categories;
            $analysis['analyzed'] = 'static+peeled';
        } else {
            $analysis['analyzed'] = 'static';
        }
    }
} catch (\PhpParser\Error $e) {
    emit_error('parse_error', $e->getMessage());
} catch (\Throwable $e) {
    error_log('phpdeobf internal_error: ' . $e->getMessage() . "\n" . $e->getTraceAsString());
    emit_error('internal_error', $e->getMessage());
}

emit(200, [
    'status' => 'ok',
    'output' => $output,
    'peeled' => $peeled,
    'analysis' => $analysis,
    'warnings' => $warnings,
    'options' => $options,
    'engine' => ['phpdeobf' => engine_version(), 'evalhook_available' => Pipeline::evalHookAvailable()],
    'elapsed_ms' => (int) round((microtime(true) - $started) * 1000),
]);
