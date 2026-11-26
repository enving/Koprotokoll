"""iPhone-Kurzbefehl für Koprotokoll erzeugen, ohne Zugangsdaten einzubetten."""

import argparse
from pathlib import Path
import plistlib
import uuid
from urllib.parse import urlsplit


def create_shortcut(endpoint: str) -> dict:
    """Audio als Multipart senden; API-Schlüssel erst beim Import abfragen.

    save_meeting=1 legt die Aufnahme auf dem Mac als Meeting ab (Audio und
    Transkript bleiben erhalten, Titel aus den ersten Worten des Transkripts),
    num_speakers=1 deaktiviert die Sprechertrennung für Monologe. Der Text landet
    trotzdem wie bisher in der Zwischenablage.
    """
    url = urlsplit(endpoint)
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('Eine HTTPS-Adresse ohne Zugangsdaten, Suchparameter oder Fragment angeben.')

    def uid():
        return str(uuid.uuid4()).upper()

    key_id, audio_id, upload_id, text_id = [uid() for _ in range(4)]

    def text(value):
        return {'WFSerializationType': 'WFTextTokenString', 'Value': {'string': value}}

    def output(identifier, name):
        return {'WFSerializationType': 'WFTextTokenAttachment',
                'Value': {'Type': 'ActionOutput', 'OutputUUID': identifier, 'OutputName': name}}

    def token_text(identifier, name):
        value = text('\ufffc')
        value['Value']['attachmentsByRange'] = {'{0, 1}': output(identifier, name)['Value']}
        return value

    def field(name, value, kind=0):
        return {'WFKey': text(name), 'WFItemType': kind, 'WFValue': value}

    def dictionary(items):
        return {'WFSerializationType': 'WFDictionaryFieldValue',
                'Value': {'WFDictionaryFieldValueItems': items}}

    def action(identifier, parameters):
        return {'WFWorkflowActionIdentifier': 'is.workflow.actions.' + identifier,
                'WFWorkflowActionParameters': parameters}

    headers = text('Bearer \ufffc')
    headers['Value']['attachmentsByRange'] = {'{7, 1}': output(key_id, 'Text')['Value']}
    audio = {'WFSerializationType': 'WFTokenAttachmentParameterState', 'Value': output(audio_id, 'Aufgenommenes Audio')}
    actions = [
        action('gettext', {'UUID': key_id, 'WFTextActionText': 'API-SCHLUESSEL-BEIM-IMPORT-EINTRAGEN'}),
        action('recordaudio', {'UUID': audio_id, 'WFRecordingStart': 'Immediately', 'WFRecordingEnd': 'On Tap'}),
        action('downloadurl', {'UUID': upload_id, 'WFURL': endpoint, 'WFHTTPMethod': 'POST',
                              'WFHTTPHeaders': dictionary([field('Authorization', headers)]),
                              'WFHTTPBodyType': 'Form', 'WFFormValues': dictionary([
                                  field('file', audio, 5), field('language', text('de')),
                                  field('save_meeting', text('1')), field('num_speakers', text('1')),
                                  field('response_format', text('text'))])}),
        action('gettext', {'UUID': text_id, 'WFTextActionText': token_text(upload_id, 'Inhalte von URL')}),
        action('setclipboard', {'WFInput': output(text_id, 'Text')}),
    ]
    return {
        'WFWorkflowClientVersion': '4110.0.1', 'WFWorkflowMinimumClientVersion': 900,
        'WFWorkflowMinimumClientVersionString': '900', 'WFWorkflowName': 'Mac-Diktat für T3',
        'WFWorkflowIcon': {'WFWorkflowIconStartColor': 4282601983, 'WFWorkflowIconGlyphNumber': 61440},
        'WFWorkflowTypes': [], 'WFWorkflowInputContentItemClasses': [],
        'WFWorkflowActions': actions,
        'WFWorkflowImportQuestions': [{'Category': 'Parameter', 'ActionIndex': 0,
                                       'ParameterKey': 'WFTextActionText',
                                       'Text': 'API-Schlüssel aus Koprotokoll eintragen. Er bleibt in deinem Kurzbefehl.',
                                       'DefaultValue': ''}],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--endpoint', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.write_bytes(plistlib.dumps(create_shortcut(args.endpoint)))
    print(f'Vorlage ohne API-Schlüssel erzeugt: {args.output.name}')


if __name__ == '__main__':
    main()
