import json
from os import access
import requests
import tarantool

from requests.structures import CaseInsensitiveDict
from datetime import datetime

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
DEFAULT_AVATAR = 'https://iupac.org/wp-content/uploads/2018/05/default-avatar.png'

db = tarantool.Connection(TARANTOOL_IP, TARANTOOL_PORT)

# Stuff for authentication via Tionix Virtual Security

awaiting_tokens = dict()

#==== CONSTANTS ====
SENTIMENT_PUB = 3
TYPE_PUB = 2
DATE_PUB = 4
CONTENT_PUB = 6

SENTIMENT_MENT = 2
DATE_MENT = 3
CONTENT_MENT = 5
FRIEND_MENT = 6

FRIEND_ID = 2
ESTIMATE_USER = 6
#===================

def filter_query(data, query, content_idx):
    tokens = set(query.split())
    return [entry for entry in data if any(token in entry[content_idx] for token in tokens)]

def filter_category(data, category, filter_value):
    return [entry for entry in data if entry[category] == filter_value]

def filter_from(data, fr, date_idx):
    fr_dt = datetime.strptime(fr, '%Y/%m/%d')
    print(fr_dt)
    data = [entry for entry in data if datetime.strptime(entry[date_idx], '%Y/%m/%d') > fr_dt]
    return data

def filter_to(data, to, date_idx):
    to_dt = datetime.strptime(to, '%Y/%m/%d')
    print(to_dt)
    data = [entry for entry in data if datetime.strptime(entry[date_idx], '%Y/%m/%d') < to_dt]
    return data

def get_friends_data(ids):
    friends = []
    for friend_id in ids:
        friends.append(list(db.select('user', friend_id)[0]))

    return friends

def get_friend_ids(user_id):
    friends = db.select(
                        'friend',
                        None,
                        values=(user_id),
                        index='secondary')
    return [entry[FRIEND_ID] for entry in friends]

def get_user_data(personal_data):
    name = personal_data['given_name']
    surname = personal_data['family_name']
    patronymic = personal_data['patronymic']
    print(name, surname, patronymic)

    user_data = db.select(
                        'user',
                        None,
                        values=(name, surname, patronymic),
                        index='secondary'
    )
    print(list(user_data))
    if len(user_data) == 1:
        return user_data[0]

    # User wasn't registered, let's place his data into database
    user_id = new_id('user')
    birthdate_parts = reversed(personal_data['birthdate'].split('/'))
    birthdate = '/'.join(birthdate_parts)
    phone = None
    try:
        phone = personal_data['mobile']
    except KeyError:
        try:
            phone = personal_data['phone']
        except KeyError:
            phone = '-'
    user_data = db.insert('user', (user_id, name, surname, patronymic, birthdate, DEFAULT_AVATAR, 9.9, personal_data['email'], phone, True, '-', '-'))
    return user_data[0]


def get_mention_impl(user_id, fr, to, sentiment):
    ment = list(db.select(
                    'mention',
                    None,
                    values=(user_id),
                    index='secondary'
    ))

    if sentiment is not None:
        ment = filter_category(ment, SENTIMENT_MENT, sentiment)
    if fr is not None:
        ment = filter_from(ment, fr, DATE_MENT)
    if to is not None:
        ment = filter_to(ment, to, DATE_MENT)

    return ment

def get_publication_impl(user_id, fr, to, sentiment, type):
    pubs = list(db.select(
                    'publication',
                    None,
                    values=(user_id),
                    index='secondary'
    ))

    if sentiment is not None:
        pubs = filter_category(pubs, SENTIMENT_PUB, sentiment)
    if type is not None:
        pubs = filter_category(pubs, TYPE_PUB, type)
    if fr is not None:
        pubs = filter_from(pubs, fr, DATE_PUB)
    if to is not None:
        pubs = filter_to(pubs, to, DATE_PUB)

    return pubs

def new_id(space):
    entries = db.select(space)
    return len(entries) + 1

def gather_personal_data(token):
    headers = CaseInsensitiveDict()
    headers["Accept"] = "application/json"
    headers["Authorization"] = "Bearer " + token
    response = requests.get(TIONIX_URL, headers=headers)

    if response.status_code != 200:
        return None
    return json.loads(response.text)

@app.route('/authenticate', methods=['GET'])
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
        'redirect_uri': 'http://45.134.255.154:32086/authenticate',
        'grant_type': 'authorization_code',
    }

    access_token = service.get_access_token(data=data, decoder=decoder)

    awaiting_tokens[session_state] = access_token

    return """<html><body>
    Эта страница скоро закроется...
    <script>
    window.opener.postMessage('{}', '*');
    console.log("Sent message to parent");
    </script>
    </body></html>""".format(access_token)

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
def checkvk(token):
    personal_data = gather_personal_data(token)

    if personal_data is None:
        return 'Auth error'
    user_id = get_user_data(personal_data)[0]

    vk_account = db.select('vk', None, values=(user_id), index='secondary', limit=1)
    return len(vk_account) > 0

@api.dispatcher.add_method
def addvk(token, id):
    personal_data = gather_personal_data(token)

    if personal_data is None:
        return 'Auth error'
    user_id = get_user_data(personal_data)[0]
    db.insert('vk', (new_id('vk'), user_id, id))
    return 'OK'

@api.dispatcher.add_method
def get_publications(token, fr, to, sentiment, type):
    personal_data = gather_personal_data(token)

    if personal_data is None:
        return 'Auth error'
    user_id = get_user_data(personal_data)[0]

    return {'publications': get_publication_impl(user_id, fr, to, sentiment, type)}

@api.dispatcher.add_method
def get_mentions(token, fr, to, sentiment):
    personal_data = gather_personal_data(token)

    if personal_data is None:
        return 'Auth error'
    user_id = get_user_data(personal_data)[0]
    return {'mentions': get_mention_impl(user_id, fr, to, sentiment)}


@api.dispatcher.add_method
def search(token, fr, to, sentiment, type, query):
    personal_data = gather_personal_data(token)

    if personal_data is None:
        return 'Auth error'
    user_id = get_user_data(personal_data)[0]
    mentions = get_mention_impl(user_id, fr, to, sentiment)
    publications = get_publication_impl(user_id, fr, to, sentiment, type)

    if query is not None:
        mentions = filter_query(mentions, query, CONTENT_MENT)
        publications = filter_query(publications, query, CONTENT_PUB)

    return {
            'mentions': mentions,
            'publications': publications
           }

@api.dispatcher.add_method
def get_friends(token):
    personal_data = gather_personal_data(token)

    if personal_data is None:
        return 'Auth error'
    user_id = get_user_data(personal_data)[0]

    ids = get_friend_ids(user_id)
    friends = get_friends_data(ids)
    estimates = [friend[ESTIMATE_USER] for friend in friends]
    try:
        mean = sum(estimates) / len(estimates)
    except ZeroDivisionError:
        mean = 10

    mentions = get_mention_impl(user_id, None, None, None)
    sentiments = []
    for mention in mentions:
        if len(mention) == 7 and mention[FRIEND_MENT] is not None:
            if mention[FRIEND_MENT] in ids:
                sentiments.append((mention[SENTIMENT_MENT] / 3) * 10)

    try:
        mean_mentions = sum(sentiments) / len(sentiments)
    except ZeroDivisionError:
        mean_mentions = 10

    return {'friends' : friends, 'mean': mean, 'mean_mentions': mean_mentions}
