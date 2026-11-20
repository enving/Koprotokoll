"""Die importierbare Vorlage darf keine privaten Zugangsdaten voraussetzen."""
import importlib.util
from pathlib import Path
import plistlib

import pytest

spec = importlib.util.spec_from_file_location('iphone_shortcut', Path(__file__).parents[1] / 'scripts/create_iphone_shortcut.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.mark.parametrize('endpoint', ['http://example.com/v1/audio/transcriptions',
                                     'https://user:password@example.com/v1/audio/transcriptions',
                                     'https://example.com/v1/audio/transcriptions?key=secret'])
def test_shortcut_rejects_insecure_or_credential_bearing_urls(endpoint):
    with pytest.raises(ValueError):
        module.create_shortcut(endpoint)


def test_shortcut_serializes_audio_file_and_response_tokens_for_apple_import():
    workflow = plistlib.loads(plistlib.dumps(module.create_shortcut('https://example.com/v1/audio/transcriptions')))
    actions = workflow['WFWorkflowActions']
    key, audio, upload, text = [a['WFWorkflowActionParameters'] for a in actions[:4]]
    form = upload['WFFormValues']['Value']['WFDictionaryFieldValueItems']
    file = form[0]
    assert file['WFItemType'] == 5
    assert file['WFValue']['Value']['Value']['OutputUUID'] == audio['UUID']
    # Ein bloßes WFTextTokenAttachment wird in Apples Textaktion leer dargestellt.
    token = text['WFTextActionText']
    assert token['WFSerializationType'] == 'WFTextTokenString'
    assert token['Value']['attachmentsByRange']['{0, 1}']['OutputUUID'] == upload['UUID']
    assert workflow['WFWorkflowImportQuestions'][0]['ActionIndex'] == 0
    assert 'BEIM-IMPORT' in key['WFTextActionText']
    assert not any('speech' in a['WFWorkflowActionIdentifier'] or 'dictate' in a['WFWorkflowActionIdentifier']
                   for a in actions)
