<?php
/**
 * Sidecar HTTP test harness.
 *
 * Boots `php -S 127.0.0.1:<port> server.php` from the project root,
 * waits for it to come up, hits each endpoint, asserts the JSON shape,
 * tears down the server. Exit code 0 = pass, non-zero = fail.
 *
 * Run: php docker/phpdeobf/tests/server_test.php
 *      (from the repo root, OR from docker/phpdeobf/)
 */

$rootDir = realpath(__DIR__ . '/..');
chdir($rootDir);

$port = 18080;
$pid = null;

function start_server($port) {
    // Fail fast if something is already listening on the port (e.g. a leaked
    // previous run). A successful fsockopen means the port is occupied.
    $probe = @fsockopen('127.0.0.1', $port, $errno, $errstr, 0.2);
    if ($probe) {
        fclose($probe);
        throw new \RuntimeException(
            "port $port already in use; run `lsof -ti:$port | xargs kill` and retry"
        );
    }

    $cmd = sprintf('php -S 127.0.0.1:%d server.php > /tmp/phpdeobf-sidecar-test.log 2>&1 & echo $!', $port);
    $pid = (int) trim(shell_exec($cmd));

    // Poll for readiness up to 5s.
    $started = microtime(true);
    while (microtime(true) - $started < 5.0) {
        $sock = @fsockopen('127.0.0.1', $port, $errno, $errstr, 0.2);
        if ($sock) {
            fclose($sock);
            return $pid;
        }
        usleep(100_000);
    }

    fwrite(STDERR, "server failed to start in 5s\n");
    fwrite(STDERR, file_get_contents('/tmp/phpdeobf-sidecar-test.log'));
    if ($pid > 0) {
        posix_kill($pid, SIGTERM);
    }
    throw new \RuntimeException("server failed to start on port $port in 5s");
}

function stop_server($pid) {
    if ($pid) {
        posix_kill($pid, SIGTERM);
    }
}

function post_json($port, $path, $body) {
    $ch = curl_init("http://127.0.0.1:$port$path");
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_POST => true,
        CURLOPT_HTTPHEADER => ['Content-Type: application/json'],
        CURLOPT_POSTFIELDS => json_encode($body),
        CURLOPT_TIMEOUT => 10,
    ]);
    $resp = curl_exec($ch);
    $code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);
    return [$code, json_decode($resp, true)];
}

function post_raw($port, $path, $rawBody) {
    $ch = curl_init("http://127.0.0.1:$port$path");
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_POST => true,
        CURLOPT_HTTPHEADER => ['Content-Type: application/json'],
        CURLOPT_POSTFIELDS => $rawBody,
        CURLOPT_TIMEOUT => 10,
    ]);
    $resp = curl_exec($ch);
    $code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);
    return [$code, json_decode($resp, true)];
}

function assert_eq($actual, $expected, $label) {
    if ($actual !== $expected) {
        throw new \RuntimeException(
            "FAIL: $label\n  expected: " . var_export($expected, true) . "\n  got:      " . var_export($actual, true)
        );
    }
    echo "ok: $label\n";
}

$exitCode = 0;
$pid = start_server($port);
try {
    // Happy path — trivial PHP, just round-trips through PrettyPrinter.
    [$code, $body] = post_json($port, '/deobfuscate', [
        'source' => "<?php echo 1 + 2;",
    ]);
    assert_eq($code, 200, 'happy path: 200');
    assert_eq($body['status'], 'ok', 'happy path: status=ok');
    if (!isset($body['output']) || !is_string($body['output'])) {
        throw new \RuntimeException("FAIL: happy path: output missing or not a string");
    }
    if (!isset($body['elapsed_ms']) || !is_int($body['elapsed_ms'])) {
        throw new \RuntimeException("FAIL: happy path: elapsed_ms missing or not int");
    }
    echo "ok: happy path: output and elapsed_ms present\n";

    // Parse error — malformed PHP.
    [$code, $body] = post_json($port, '/deobfuscate', [
        'source' => "<?php this is not <<<< valid",
    ]);
    assert_eq($code, 200, 'parse error: 200');
    assert_eq($body['status'], 'error', 'parse error: status=error');
    assert_eq($body['code'], 'parse_error', 'parse error: code=parse_error');

    // Input too large — bigger than the 5 MB default cap.
    // 750 000 reps × 8 bytes = ~6 MB source; JSON-encoded ≈ 7 MB, under PHP's 8 MB post_max_size.
    $bigSource = "<?php\n" . str_repeat("\$x = 1;\n", 750_000);  // ~6 MB
    [$code, $body] = post_json($port, '/deobfuscate', [
        'source' => $bigSource,
    ]);
    assert_eq($code, 200, 'too large: 200');
    assert_eq($body['status'], 'error', 'too large: status=error');
    assert_eq($body['code'], 'input_too_large', 'too large: code=input_too_large');

    // Bad request — non-JSON body.
    [$code, $body] = post_raw($port, '/deobfuscate', 'not json at all');
    assert_eq($code, 400, 'bad request (non-json): 400');
    assert_eq($body['code'], 'bad_request', 'bad request (non-json): code=bad_request');

    // Bad request — missing source field.
    [$code, $body] = post_json($port, '/deobfuscate', ['filename' => 'x.php']);
    assert_eq($code, 400, 'bad request (missing source): 400');
    assert_eq($body['code'], 'bad_request', 'bad request (missing source): code=bad_request');

    // Not found — wrong route.
    [$code, $body] = post_json($port, '/wrong/path', ['source' => '<?php echo 1;']);
    assert_eq($code, 404, 'not found: 404');
    assert_eq($body['code'], 'not_found', 'not found: code=not_found');

    echo "\nALL PASSED\n";
} catch (\Throwable $e) {
    fwrite(STDERR, $e->getMessage() . "\n");
    $exitCode = 1;
} finally {
    stop_server($pid);
}
exit($exitCode);
