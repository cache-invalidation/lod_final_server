import json
import requests
import tarantool

from requests.structures import CaseInsensitiveDict

from flask import Flask, request
from flask_cors import CORS, cross_origin
from jsonrpc.backend.flask import api

from urllib import parse
from rauth.service import OAuth2Service

from credentials import CLIENT_ID, CLIENT_SECRET, TARANTOOL_IP, TARANTOOL_PORT

app = Flask(__name__)
app.register_blueprint(api.as_blueprint())

CORS(app)

cors = CORS(app, resource={
    r"/*": {
        "origins": "*"
    }
})

TIONIX_URL = 'https://tvscp.tionix.ru/realms/master/protocol/openid-connect/userinfo'

db = tarantool.Connection(TARANTOOL_IP, TARANTOOL_PORT)

# Stuff for authentication via Tionix Virtual Security

awaiting_tokens = dict()

def get_user_data(personal_data):
    name = personal_data['given_name']
    surname = personal_data['family_name']
    patronymic = personal_data['patronymic']

    user_data = db.select(
                        'user',
                        None,
                        values=(name, surname, patronymic),
                        index='secondary',
                        limit=1
    )[0]
    return user_data

def gather_personal_data(token):
    headers = CaseInsensitiveDict()
    headers["Accept"] = "application/json"
    headers["Authorization"] = "Bearer " + token
    response = requests.get(TIONIX_URL, headers=headers)

    if response.status_code != 200:
        return None
    return json.loads(response.text)

@app.route('/authenticate', methods=['GET'])
@cross_origin()
def authenticator():
    def decoder(str): return json.loads(str.decode())

    service = OAuth2Service(client_id=CLIENT_ID,
                            client_secret=CLIENT_SECRET,
                            name='tvscp',
                            authorize_url='https://tvscp.tionix.ru/realms/master/protocol/openid-connect/auth',
                            access_token_url='https://tvscp.tionix.ru/realms/master/protocol/openid-connect/token')

    query_string = request.query_string.decode()
    parsed = parse.parse_qs(query_string)

    if 'code' not in parsed:
        return "Что-то пошло не так :c"

    code = parsed['code'][0]
    session_state = parsed['session_state'][0]

    data = {
        'code': code,
        'redirect_uri': 'http://127.0.0.1:5000/authenticate',
        'grant_type': 'authorization_code',
    }

    access_token = service.get_access_token(data=data, decoder=decoder)

    awaiting_tokens[session_state] = access_token

    return 'Совсем скоро это окно закроется...'


@api.dispatcher.add_method
def get_access_token(session_state):
    if session_state not in awaiting_tokens:
        return {'status': 0}

    token = awaiting_tokens[session_state]
    del awaiting_tokens[session_state]
    return {'status': 1, 'access_token': token}

@api.dispatcher.add_method
def get_user_info(token):
    personal_data = gather_personal_data(token)

    if personal_data is None:
        return 'Auth error'
    return get_user_data(personal_data)



@api.dispatcher.add_method
def get_publications(token, fr, to, sentiment, type):
    personal_data = gather_personal_data(token)

    if personal_data is None:
        return 'Auth error'
    user_id = get_user_data(personal_data)[0]

    pubs = db.select(
                    'publication',
                    None,
                    values=(user_id),
                    index='secondary'
    )

    return {'publications': list(pubs)}
