import os

import requests
from flask import Flask, request
from marshmallow import Schema, fields, validate

NETWORK_SERVICE_URL = os.environ.get("NETWORK_SERVICE_URL", "http://network_service:8000/job")


app = Flask(__name__)


class LocationSchema(Schema):
    latitude = fields.Float(allow_none=True)
    longitude = fields.Float(allow_none=True)


class SkillSchema(Schema):
    subject = fields.Str(required=True)
    subject_id = fields.Integer(required=True)
    category = fields.Str(required=True)
    qual_level = fields.Str(required=True)
    qual_level_id = fields.Integer(required=True)
    qual_level_ranking = fields.Float(load_default=0)  # load_default, not default (default is the dump-side value)


class Model(Schema):
    id = fields.Integer(required=True)
    client_name = fields.Str(validate=validate.Length(max=255), required=True)
    sort_index = fields.Float(required=True)
    client_phone = fields.Str(validate=validate.Length(max=255), allow_none=True)

    location = fields.Nested(LocationSchema)

    contractor = fields.Integer(validate=validate.Range(min=0), allow_none=True)
    upstream_http_referrer = fields.Str(validate=validate.Length(max=1023), allow_none=True)
    grecaptcha_response = fields.Str(validate=validate.Length(min=20, max=1000), required=True)
    last_updated = fields.DateTime(allow_none=True)

    skills = fields.Nested(SkillSchema, many=True)


model_schema = Model()


@app.route("/api/create", methods=["POST"])
def create():
    json_data = request.get_json()
    model_schema.load(json_data)  # validates (raises on invalid); result intentionally unused
    return {"success": True}, 201


@app.route("/api/create_async", methods=["POST"])
async def create_async():
    json_data = request.get_json()
    model_schema.load(json_data)  # validates (raises on invalid); result intentionally unused
    return {"success": True}, 201


@app.route("/api/iojob", methods=["GET"])
def iojob():
    response = requests.get(NETWORK_SERVICE_URL)
    response.raise_for_status()
    return {"success": True}, 200
