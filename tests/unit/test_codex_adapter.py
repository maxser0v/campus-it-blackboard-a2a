import asyncio
import json
import sys

import pytest

from campus_blackboard.codex_adapter import CodexCLIAdapter
from campus_blackboard.config import Settings
from campus_blackboard.model_adapter import ModelAdapterError, OfflineFixtureAdapter, model_from_settings


@pytest.fixture
def fake_codex(tmp_path):
    path = tmp_path / 'fake codex'
    path.write_text('#!' + sys.executable + '\n' + '''
import sys, json, os, time
from pathlib import Path
args=sys.argv[1:]
assert args[0]=='exec'
assert '--ignore-user-config' in args and '--ephemeral' in args
if '--model' in args and args[args.index('--model')+1]=='gpt-6-luna':
 assert 'model_reasoning_effort=\"high\"' in args
assert args[args.index('--sandbox')+1]=='read-only'
assert 'shell_tool' in args and 'multi_agent' in args
assert not any(k.startswith('CAMPUS_') for k in os.environ)
assert 'OPENAI_API_KEY' not in os.environ
prompt=sys.stdin.read()
assert 'output_schema' in prompt
schema=json.loads(Path(args[args.index('--output-schema')+1]).read_text())
assert schema['additionalProperties'] is False
if 'simulate-timeout' in prompt: time.sleep(20)
if 'simulate-exit' in prompt: sys.exit(2)
if 'simulate-failed-turn' in prompt:
 print(json.dumps({'type':'turn.failed','error':{'message':'failed'}}));sys.exit(0)
output=Path(args[args.index('--output-last-message')+1])
if 'simulate-missing' not in prompt:
 output.write_text('not json' if 'simulate-invalid' in prompt else json.dumps({'result':'{"ok":true}'}))
print(json.dumps({'type':'turn.completed', 'usage':{'input_tokens':100,'output_tokens':20,'cached_input_tokens':10}}))
''')
    path.chmod(0o700)
    return str(path)


def invoke(adapter, instruction='Return JSON'):
    return asyncio.run(adapter.complete(role='review', purpose='test', instructions=instruction, context={}, output_schema={'ok':'boolean'}))


def test_codex_selection_ignores_groq_model_and_key():
    adapter = model_from_settings(Settings(model_mode='live', model_provider='codex_cli', model_name='groq-only-model', model_api_key='secret', codex_model='chosen-codex-model', codex_timeout_seconds=44))
    assert isinstance(adapter, CodexCLIAdapter)
    assert adapter.model == 'chosen-codex-model'
    assert adapter.timeout == 44
    assert isinstance(model_from_settings(Settings(model_provider='codex_cli')), OfflineFixtureAdapter)


def test_codex_structured_output_usage_and_secret_isolation(fake_codex, monkeypatch):
    monkeypatch.setenv('CAMPUS_MODEL_API_KEY', 'must-not-pass')
    monkeypatch.setenv('OPENAI_API_KEY', 'must-not-pass')
    adapter = CodexCLIAdapter(fake_codex)
    assert json.loads(invoke(adapter)) == {'ok': True}
    assert adapter.last_call_metadata['usage']['total_tokens'] == 120
    assert adapter.last_call_metadata['provider'] == 'codex_cli'


@pytest.mark.parametrize(('instruction','message'), [
    ('simulate-exit','status 2'), ('simulate-invalid','invalid JSON'),
    ('simulate-missing','no final response'), ('simulate-failed-turn','failed turn'),
])
def test_codex_failures_surface_without_fake_output(fake_codex, instruction, message):
    adapter = CodexCLIAdapter(fake_codex)
    with pytest.raises(ModelAdapterError, match=message): invoke(adapter, instruction)
    assert adapter.last_call_metadata['usage'] is None


def test_codex_timeout_terminates_process(fake_codex):
    with pytest.raises(ModelAdapterError, match='timed out'):
        invoke(CodexCLIAdapter(fake_codex, timeout=0.2), 'simulate-timeout')


def test_codex_missing_executable_is_actionable():
    with pytest.raises(ModelAdapterError, match='not found'):
        invoke(CodexCLIAdapter('/missing/codex'))


@pytest.mark.parametrize('kwargs', [{'model_provider':'unknown'}, {'codex_timeout_seconds':0}, {'codex_executable':''}])
def test_bad_codex_settings_rejected(kwargs):
    with pytest.raises(ValueError): Settings(**kwargs)


def test_codex_env_settings_and_specialist_timeout(monkeypatch):
    monkeypatch.setenv('CAMPUS_MODEL_MODE', 'live')
    monkeypatch.setenv('CAMPUS_MODEL_PROVIDER', 'codex_cli')
    monkeypatch.setenv('CAMPUS_CODEX_MODEL', 'codex-selected')
    monkeypatch.setenv('CAMPUS_CODEX_TIMEOUT_SECONDS', '100')
    monkeypatch.delenv('CAMPUS_MODEL_BASE_URL', raising=False)
    monkeypatch.delenv('CAMPUS_MODEL_NAME', raising=False)
    settings = Settings.from_env(load_dotenv_file=False)
    assert settings.codex_model == 'codex-selected'
    assert settings.specialist_timeout >= 230
    assert isinstance(model_from_settings(settings), CodexCLIAdapter)


def test_reasoning_effort_passed_to_codex(fake_codex):
    adapter = model_from_settings(Settings(model_mode='live', model_provider='codex_cli', codex_model='gpt-6-luna', codex_reasoning_effort='high', codex_executable=fake_codex))
    assert adapter.reasoning_effort == 'high'
    assert json.loads(invoke(adapter)) == {'ok': True}


def test_invalid_codex_reasoning_rejected():
    with pytest.raises(ValueError, match='reasoning effort'):
        Settings(codex_reasoning_effort='invalid')
