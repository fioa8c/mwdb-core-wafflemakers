<?php
/*
 * Benign test fixture for dev/malleus-smoke.py (written by hand, not from any sample).
 * Intended to match exactly one weak/exploratory rule: php_generic_unlink_self
 * (exploratory, severity 2) and no strong (production, severity >= 3) rule.
 */

/**
 * One-time setup helper: removes its own file once the setup has run.
 */
function wm_example_cleanup_setup_script() {
    if ( get_option( 'wm_example_setup_done' ) ) {
        unlink( __FILE__ );
    }
}
add_action( 'shutdown', 'wm_example_cleanup_setup_script' );
