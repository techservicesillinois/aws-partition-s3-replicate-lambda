import json

import boto3
import pytest

import partition_s3_replicate

def test_get_dst_creds(setup_accounts, iam_dst_creds):
    creds = partition_s3_replicate.get_dst_creds()

    assert creds['aws_access_key_id'] == iam_dst_creds['AccessKeyId']
    assert creds['aws_secret_access_key'] == iam_dst_creds['SecretAccessKey']

@pytest.mark.parametrize("event, expect_queued", [
    pytest.param(
        {
            'detail-type': 'Object Created',
            'detail': {
                'object': {'key': 'foo.txt', 'version-id': 'IYV3p45BT0ac8hjHg1houSdS1a.Mro8e'},
                'reason': 'PutObject'
            }
        },
        True,
        id='Object Created (PutObject)'
    ),
    pytest.param(
        {
            'detail-type': 'Object Created',
            'detail': {
                'object': {'key': 'foo.txt', 'version-id': 'IYV3p45BT0ac8hjHg1houSdS1a.Mro8e'},
                'reason': 'POST Object'
            }
        },
        True,
        id='Object Created (POST Object)'
    ),
    pytest.param(
        {
            'detail-type': 'Object Created',
            'detail': {
                'object': {'key': 'foo.txt', 'version-id': 'IYV3p45BT0ac8hjHg1houSdS1a.Mro8e'},
                'reason': 'CopyObject'
            }
        },
        True,
        id='Object Created (CopyObject)'
    ),
    pytest.param(
        {
            'detail-type': 'Object Created',
            'detail': {
                'object': {'key': 'foo.txt', 'version-id': 'IYV3p45BT0ac8hjHg1houSdS1a.Mro8e'},
                'reason': 'CompleteMultipartUpload'
            }
        },
        True,
        id='Object Created (CompleteMultipartUpload)'
    ),
    pytest.param(
        {
            'detail-type': 'Object Deleted',
            'detail': {
                'object': {'key': 'foo.txt', 'version-id': 'IYV3p45BT0ac8hjHg1houSdS1a.Mro8e'},
                'reason': 'DeleteObject'
            }
        },
        True,
        id='Object Deleted (DeleteObject)'
    ),
    pytest.param(
        {
            'detail-type': 'Object Deleted',
            'detail': {
                'object': {'key': 'foo.txt', 'version-id': 'IYV3p45BT0ac8hjHg1houSdS1a.Mro8e'},
                'reason': 'Lifecycle Expiration'
            }
        },
        True,
        id='Object Deleted (Lifecycle Expiration)'
    ),
    pytest.param(
        {
            'detail-type': 'Object Created',
            'detail': {
                'object': {'key': 'foo.txt', 'version-id': 'IYV3p45BT0ac8hjHg1houSdS1a.Mro8e'}
            }
        },
        True,
        id='Object Tags Added'
    ),
    pytest.param(
        {
            'detail-type': 'Object Created',
            'detail': {
                'object': {'key': 'foo.txt', 'version-id': 'IYV3p45BT0ac8hjHg1houSdS1a.Mro8e'}
            }
        },
        True,
        id='Object Tags Deleted'
    ),

    pytest.param(
        {
            'detail-type': 'Object Restore Initiated',
            'detail': {
                'object': {'key': 'foo.txt', 'version-id': 'IYV3p45BT0ac8hjHg1houSdS1a.Mro8e'}
            }
        },
        False,
        id='Object Restore Initiated'
    ),
    pytest.param(
        {
            'detail-type': 'Object Restore Completed',
            'detail': {
                'object': {'key': 'foo.txt', 'version-id': 'IYV3p45BT0ac8hjHg1houSdS1a.Mro8e'}
            }
        },
        False,
        id='Object Restore Completed'
    ),
    pytest.param(
        {
            'detail-type': 'Object Restore Expired',
            'detail': {
                'object': {'key': 'foo.txt', 'version-id': 'IYV3p45BT0ac8hjHg1houSdS1a.Mro8e'}
            }
        },
        False,
        id='Object Restore Expired'
    ),

    pytest.param(
        {
            'detail-type': 'Object Storage Class Changed',
            'detail': {
                'object': {'key': 'foo.txt', 'version-id': 'IYV3p45BT0ac8hjHg1houSdS1a.Mro8e'}
            }
        },
        False,
        id='Object Storage Class Changed'
    ),
    pytest.param(
        {
            'detail-type': 'Object Access Tier Changed',
            'detail': {
                'object': {'key': 'foo.txt', 'version-id': 'IYV3p45BT0ac8hjHg1houSdS1a.Mro8e'}
            }
        },
        False,
        id='Object Access Tier Changed'
    ),
    pytest.param(
        {
            'detail-type': 'Object ACL Updated',
            'detail': {
                'object': {'key': 'foo.txt', 'version-id': 'IYV3p45BT0ac8hjHg1houSdS1a.Mro8e'}
            }
        },
        False,
        id='Object ACL Updated'
    ),
])
def test_event_handler(setup_accounts, lambda_context, event, expect_queued):
    partition_s3_replicate.event_handler(event, lambda_context)

    queue = boto3.resource('sqs').Queue(partition_s3_replicate.OBJECTS_QUEUE)
    msgs = queue.receive_messages(AttributeNames=['All'], WaitTimeSeconds=0)

    if expect_queued:
        assert msgs
        msg = msgs[0]

        msg_body = json.loads(msg.body)
        assert msg_body == event

        assert msg.attributes['MessageGroupId'] == event['detail']['object']['key']
    else:
        assert not msgs
