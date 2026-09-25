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
set_time_limit(120);

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
    echo json_encode($payload);
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
        $analyzed = $peeled !== null ? 'peeled' : 'static';
        $codeToAnalyze = $peeled !== null ? $peeled['output'] : $output;
        $deobf = new Deobfuscator();
        $findings = $deobf->analyze($codeToAnalyze, $peeled !== null ? null : $tree);
        $formatter = new ReportFormatter();
        $analysis = json_decode($formatter->formatJson($findings, basename($filename)), true);
        $analysis['analyzed'] = $analyzed;
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
