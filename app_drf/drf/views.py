import json
import os
import requests
from adrf.decorators import api_view as async_api_view
from rest_framework import serializers
from rest_framework.decorators import api_view
from rest_framework.response import Response

NETWORK_SERVICE_URL = os.environ.get('NETWORK_SERVICE_URL', 'http://network_service:8000/job')


class Location(serializers.Serializer):
    latitude = serializers.FloatField(required=False, allow_null=True)
    longitude = serializers.FloatField(required=False, allow_null=True)


class Skill(serializers.Serializer):
    subject = serializers.CharField()
    subject_id = serializers.IntegerField()
    category = serializers.CharField()
    qual_level = serializers.CharField()
    qual_level_id = serializers.IntegerField()
    qual_level_ranking = serializers.FloatField(default=0)


class Model(serializers.Serializer):
    id = serializers.IntegerField()
    client_name = serializers.CharField(max_length=255, trim_whitespace=False)
    sort_index = serializers.FloatField()
    client_phone = serializers.CharField(
        max_length=255, trim_whitespace=False, required=False, allow_null=True
    )

    location = Location(required=False, allow_null=True)

    contractor = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    upstream_http_referrer = serializers.CharField(
        max_length=1023, trim_whitespace=False, required=False, allow_null=True
    )
    grecaptcha_response = serializers.CharField(
        min_length=20, max_length=1000, trim_whitespace=False
    )
    last_updated = serializers.DateTimeField(required=False, allow_null=True)

    skills = serializers.ListField(child=Skill())


@api_view(['POST'])
def create(request):
    data = Model(data=json.loads(request.body))
    assert data.is_valid()
    return Response({'success': True})


# Async DRF via the `adrf` package (async-capable views), same serializer/validation.
@async_api_view(['POST'])
async def create_async(request):
    data = Model(data=json.loads(request.body))
    assert data.is_valid()
    return Response({'success': True})


@api_view(['GET'])
def iojob(request):
    response = requests.get(NETWORK_SERVICE_URL)
    assert response.status_code == 200
    return Response({'success': True})
