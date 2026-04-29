<?php
/**
 * HTTP sidecar wrapper around the PHPDeobfuscator pipeline.
 *
 * Endpoint:
 *   POST /deobfuscate
 *   Content-Type: application/json
 *   Body: {"source": "<php source>", "filename": "input.php"}
 *
 *   200 {"status":"ok","output":"...","elapsed_ms":N}
 *   200 {"status":"error","code":"parse_error|input_too_large|internal_error","message":"..."}
 *
 * Designed for `php -S` (single-process built-in server). Concurrent
 * requests serialise — fine for interactive use.
 */

require __DIR__ . '/vendor/autoload.php';

ini_set('xdebug.var_display_max_depth', -1);
ini_set('memory_limit', '512M');
ini_set('xdebug.max_nesting_level', 1000);

$MAX_INPUT_BYTES = (int) (getenv('MAX_INPUT_BYTES') ?: 5 * 1024 * 1024);

function emit($status, $payload) {
    http_response_code($status);
    header('Content-Type: application/json');
    echo json_encode($payload);
    exit;
}

function emit_error($code, $message) {
    emit(200, ['status' => 'error', 'code' => $code, 'message' => $message]);
}

// Only POST /deobfuscate is supported. Anything else → 404.
if ($_SERVER['REQUEST_METHOD'] !== 'POST' || $_SERVER['REQUEST_URI'] !== '/deobfuscate') {
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
if (strlen($source) > $MAX_INPUT_BYTES) {
    emit_error('input_too_large', sprintf(
        'source is %d bytes; cap is %d',
        strlen($source),
        $MAX_INPUT_BYTES
    ));
}
if (!is_string($filename) || $filename === '') {
    $filename = 'input.php';
}

$started = microtime(true);

try {
    $deobf = new \PHPDeobfuscator\Deobfuscator(false);
    $virtualPath = '/var/www/html/' . basename($filename);
    $deobf->getFilesystem()->write($virtualPath, $source);
    $deobf->setCurrentFilename($virtualPath);
    $tree = $deobf->parse($source);
    $tree = $deobf->deobfuscate($tree);
    $output = $deobf->prettyPrint($tree);
} catch (\PhpParser\Error $e) {
    emit_error('parse_error', $e->getMessage());
} catch (\Throwable $e) {
    error_log('phpdeobf internal_error: ' . $e->getMessage() . "\n" . $e->getTraceAsString());
    emit_error('internal_error', $e->getMessage());
}

$elapsedMs = (int) round((microtime(true) - $started) * 1000);

emit(200, [
    'status' => 'ok',
    'output' => $output,
    'elapsed_ms' => $elapsedMs,
]);
