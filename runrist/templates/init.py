from runrist.api import setq

setq(ai_backends={
    'claude': {
        'command': ['claude', '-p', '--output-format', 'text', '--tools', '', '--strict-mcp-config'],
        'model_args': ['--model', '{value}'],
        'models': [],  # Add model IDs for completion, or enter one in the picker.
        'effort_args': ['--effort', '{value}'],
        'efforts': ['low', 'medium', 'high', 'xhigh', 'max'],
        'login': {
            'Claude subscription': ['auth', 'login'],
            'Anthropic Console': ['auth', 'login', '--console'],
            'Single sign-on (SSO)': ['auth', 'login', '--sso'],
        },
        'auth_status': ['auth', 'status', '--text'],
    },
    'codex': {
        'command': ['codex', 'exec', '--sandbox', 'read-only', '--skip-git-repo-check',
                    '--ephemeral', '--color', 'never', '-'],
        'model_args': ['--model', '{value}'],
        'models': [],
        'effort_args': ['-c', 'model_reasoning_effort={quoted_value}'],
        'efforts': ['none', 'minimal', 'low', 'medium', 'high', 'xhigh'],
        'login': {
            'Browser sign-in': ['login'],
            'Device code': ['login', '--device-auth'],
            'API key from OPENAI_API_KEY': {
                'args': ['login', '--with-api-key'], 'stdin_env': 'OPENAI_API_KEY',
            },
        },
        'auth_status': ['login', 'status'],
    },
    # Add your own command or HTTP profiles here.
})
# setq(ai_backend='codex')
