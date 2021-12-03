import json

from flask import Flask
from flask_cors import CORS
from jsonrpc.backend.flask import api

app = Flask(__name__)
app.register_blueprint(api.as_blueprint())

CORS(app)

cors = CORS(app, resource={
    r"/*":{
        "origins":"*"
    }
})
