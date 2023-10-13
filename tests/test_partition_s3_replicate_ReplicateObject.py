from datetime import datetime
from io import BytesIO

import botocore.client
from botocore.exceptions import ClientError
import pytest

import partition_s3_replicate

@pytest.fixture
def replicate_object(request, setup_accounts):
    detail = getattr(
        request,
        'param',
        {
            'bucket': {'name': 'source-bucket'},
            'object': {
                'key': 'foo.txt',
                'version-id': 'IYV3p45BT0ac8hjHg1houSdS1a.Mro8e',
            },
            'reason': 'PutObject'
        }
    )
    detail.setdefault('bucket', {})
    detail['bucket'].setdefault('name', 'source-bucket')

    return partition_s3_replicate.ReplicateObject(detail=detail)

def test_bucket_name(replicate_object):
    assert replicate_object.bucket_name == 'source-bucket'

def test_dst_session(replicate_object, sts_client):
    sts_clnt = replicate_object._dst_session.client('sts')
    res = sts_clnt.get_caller_identity()

    assert res['Account'] == '999999999999'

def test_dst_bucket(replicate_object):
    assert replicate_object.dst_bucket.name == 'dest-bucket'

@pytest.mark.parametrize('obj_key', [
    pytest.param('foo.txt'),
    pytest.param('bar.txt'),
    pytest.param('baz.txt'),
])
def test_dst_object_curr(setup_s3, setup_s3_destobjs, obj_key):
    obj_ver = setup_s3[obj_key][-1]['VersionId']
    detail = {
        'bucket': {'name': 'source-bucket'},
        'object': {'key': obj_key, 'version-id': obj_ver},
    }

    replicate_object = partition_s3_replicate.ReplicateObject(detail=detail)

    expected_obj = setup_s3_destobjs[obj_key][-1].copy()
    expected_obj.pop('content', None)
    expected_obj.pop('tags', None)
    assert replicate_object.dst_object_curr == expected_obj

def test_dst_object_curr_notfound(setup_s3, setup_s3_destobjs):
    detail = {
        'bucket': {'name': 'source-bucket'},
        'object': {'key': 'does-not-exist.txt', 'version-id': '123'},
    }

    replicate_object = partition_s3_replicate.ReplicateObject(detail=detail)

    with pytest.raises(ClientError) as exc_info:
        replicate_object.dst_object_curr
    assert exc_info.value.response['Error']['Code'] in ('404', 'NotFound')

def test_key(replicate_object):
    assert replicate_object.key == 'foo.txt'

@pytest.mark.parametrize('obj_key, obj_ver', [
    pytest.param('foo.txt', 0),
    pytest.param('bar.txt', 0),
    pytest.param('bar.txt', 1),
    pytest.param('baz.txt', 0),
    pytest.param('baz.txt', 1),
    pytest.param('baz.txt', 2),
])
def test_object_item(setup_s3, setup_s3_destobjs, obj_key, obj_ver):
    obj_ver = setup_s3[obj_key][obj_ver]['VersionId']
    detail = {
        'bucket': {'name': 'source-bucket'},
        'object': {'key': obj_key, 'version-id': obj_ver},
    }

    replicate_object = partition_s3_replicate.ReplicateObject(detail=detail)

    expected = replicate_object.objects_table.get_item(
        Key={'Key': obj_key, 'VersionId': obj_ver}
    )['Item']
    assert replicate_object.object_item == (expected['DestObject'], expected['DestObjectTags'])

def test_object_item_notfound(setup_s3, setup_s3_destobjs):
    detail = {
        'bucket': {'name': 'source-bucket'},
        'object': {'key': 'does-not-exist.txt', 'version-id': '123'},
    }

    replicate_object = partition_s3_replicate.ReplicateObject(detail=detail)

    assert replicate_object.object_item == ({}, {})

def test_src_session(replicate_object, sts_client):
    sts_clnt = replicate_object._src_session.client('sts')
    res = sts_clnt.get_caller_identity()

    assert res['Account'] == '123456789012'

def test_src_bucket(replicate_object):
    assert replicate_object.src_bucket.name == 'source-bucket'

@pytest.mark.parametrize('obj_key, obj_ver', [
    pytest.param('foo.txt', 0),
    pytest.param('bar.txt', 0),
    pytest.param('bar.txt', 1),
    pytest.param('baz.txt', 0),
    pytest.param('baz.txt', 1),
    pytest.param('baz.txt', 2),
])
def test_src_object(setup_s3, obj_key, obj_ver):
    obj_data = setup_s3[obj_key][obj_ver]
    obj_ver = obj_data['VersionId']
    detail = {
        'bucket': {'name': 'source-bucket'},
        'object': {'key': obj_key, 'version-id': obj_ver},
    }

    replicate_object = partition_s3_replicate.ReplicateObject(detail=detail)

    expected_obj = obj_data.copy()
    expected_obj.pop('content', None)
    expected_obj.pop('tags', None)
    assert replicate_object.src_object == expected_obj

def test_src_object_notfound(setup_s3):
    detail = {
        'bucket': {'name': 'source-bucket'},
        'object': {'key': 'does-not-exist.txt', 'version-id': '123'},
    }

    replicate_object = partition_s3_replicate.ReplicateObject(detail=detail)

    with pytest.raises(ClientError) as exc_info:
        replicate_object.src_object
    assert exc_info.value.response['Error']['Code'] in ('404', 'NotFound')

@pytest.mark.parametrize('obj_key, obj_ver', [
    pytest.param('foo.txt', 0),
    pytest.param('bar.txt', 0),
    pytest.param('bar.txt', 1),
    pytest.param('baz.txt', 0),
    pytest.param('baz.txt', 1),
    pytest.param('baz.txt', 2),
])
def test_src_object_tags(setup_s3, obj_key, obj_ver):
    obj_data = setup_s3[obj_key][obj_ver]
    obj_ver = obj_data['VersionId']
    detail = {
        'bucket': {'name': 'source-bucket'},
        'object': {'key': obj_key, 'version-id': obj_ver},
    }

    replicate_object = partition_s3_replicate.ReplicateObject(detail=detail)

    expected_tags = obj_data['tags']
    assert replicate_object.src_object_tags == expected_tags

def test_src_object_tags_notfound(setup_s3):
    detail = {
        'bucket': {'name': 'source-bucket'},
        'object': {'key': 'does-not-exist.txt', 'version-id': '123'},
    }

    replicate_object = partition_s3_replicate.ReplicateObject(detail=detail)

    with pytest.raises(ClientError) as exc_info:
        replicate_object.src_object_tags
    assert exc_info.value.response['Error']['Code'] in ('404', 'NotFound', 'NoSuchVersion')

def test_version_id(replicate_object):
    assert replicate_object.version_id == 'IYV3p45BT0ac8hjHg1houSdS1a.Mro8e'

@pytest.mark.parametrize('obj_key, obj_ver_idx', [
    pytest.param('foo.txt', 0),
    pytest.param('bar.txt', 0),
    pytest.param('bar.txt', 1),
    pytest.param('baz.txt', 0),
    pytest.param('baz.txt', 1),
    pytest.param('baz.txt', 2),
])
def test_handle_create(setup_s3, obj_key, obj_ver_idx):
    obj_data = setup_s3[obj_key][obj_ver_idx]
    obj_ver = obj_data['VersionId']
    detail = {
        'bucket': {'name': 'source-bucket'},
        'object': {'key': obj_key, 'version-id': obj_ver},
    }

    replicate_object = partition_s3_replicate.ReplicateObject(detail)
    assert 'Item' not in replicate_object.objects_table.get_item(
        Key={'Key': obj_key, 'VersionId': obj_ver}
    )

    replicate_object.handle_created()

    # Check that the content is correct
    dst_content = BytesIO()
    replicate_object.dst_bucket.download_fileobj(
        Fileobj=dst_content,
        Key=obj_key,
    )
    assert dst_content.getvalue() == obj_data['content']

    # Get the object data and its tags to check later
    dst_object = replicate_object._dst_s3_clnt.head_object(
        Bucket=partition_s3_replicate.DST_BUCKET,
        Key=obj_key,
    )
    res = replicate_object._dst_s3_clnt.get_object_tagging(
        Bucket=partition_s3_replicate.DST_BUCKET,
        Key=obj_key,
    )
    dst_object_tags = {t['Key']: t['Value'] for t in res.get('TagSet', [])}

    # Get the object data from the DynamoDB table to check later
    obj_item = replicate_object.objects_table.get_item(
        Key={'Key': obj_key, 'VersionId': obj_ver}
    )['Item']
    assert obj_item
    assert obj_item['DestObject']['ServerSideEncryption'] == 'aws:kms'
    assert obj_item['DestObject']['SSEKMSKeyId'] == partition_s3_replicate.DST_KMS_KEY

    if 'Expires' in obj_item:
        obj_item['Expires'] = datetime.fromisoformat(obj_item['Expires'])

    # Check fields that we replicate, that they are correct in the destination
    # and also the object table.
    for name in ['CacheControl', 'Expires', 'ContentDisposition', 'ContentEncoding', 'ContentLanguage', 'ContentType', 'Metadata']:
        if name in obj_data:
            assert dst_object.get(name) == obj_data[name]
            assert obj_item['DestObject'].get(name) == obj_data[name]
        else:
            assert name not in dst_object
            assert name not in obj_item['DestObject']

    assert dst_object_tags == obj_data['tags']
    assert obj_item['DestObjectTags'] == obj_data['tags']

@pytest.mark.parametrize('setup_s3', [
    pytest.param({'versioning': False}, id='nonversioned')
], indirect=True)
@pytest.mark.parametrize('obj_key', [
    pytest.param('foo.txt'),
    pytest.param('bar.txt'),
    pytest.param('baz.txt'),
])
def test_handle_create_nonversioned(setup_s3, obj_key):
    obj_data = setup_s3[obj_key][-1]
    detail = {
        'bucket': {'name': 'source-bucket'},
        'object': {'key': obj_key},
    }

    replicate_object = partition_s3_replicate.ReplicateObject(detail)
    assert 'Item' not in replicate_object.objects_table.get_item(
        Key={'Key': obj_key, 'VersionId': '$null'}
    )

    replicate_object.handle_created()

    # Check that the content is correct
    dst_content = BytesIO()
    replicate_object.dst_bucket.download_fileobj(
        Fileobj=dst_content,
        Key=obj_key,
    )
    assert dst_content.getvalue() == obj_data['content']

    # Get the object data and its tags to check later
    dst_object = replicate_object._dst_s3_clnt.head_object(
        Bucket=partition_s3_replicate.DST_BUCKET,
        Key=obj_key,
    )
    res = replicate_object._dst_s3_clnt.get_object_tagging(
        Bucket=partition_s3_replicate.DST_BUCKET,
        Key=obj_key,
    )
    dst_object_tags = {t['Key']: t['Value'] for t in res.get('TagSet', [])}

    # Get the object data from the DynamoDB table to check later
    obj_item = replicate_object.objects_table.get_item(
        Key={'Key': obj_key, 'VersionId': '$null'}
    )['Item']
    assert obj_item
    assert obj_item['DestObject']['ServerSideEncryption'] == 'aws:kms'
    assert obj_item['DestObject']['SSEKMSKeyId'] == partition_s3_replicate.DST_KMS_KEY

    if 'Expires' in obj_item:
        obj_item['Expires'] = datetime.fromisoformat(obj_item['Expires'])

    # Check fields that we replicate, that they are correct in the destination
    # and also the object table.
    for name in ['CacheControl', 'Expires', 'ContentDisposition', 'ContentEncoding', 'ContentLanguage', 'ContentType', 'Metadata']:
        if name in obj_data:
            assert dst_object.get(name) == obj_data[name]
            assert obj_item['DestObject'].get(name) == obj_data[name]
        else:
            assert name not in dst_object
            assert name not in obj_item['DestObject']

    assert dst_object_tags == obj_data['tags']
    assert obj_item['DestObjectTags'] == obj_data['tags']

def test_handle_create_notfound(setup_s3):
    detail = {
        'bucket': {'name': 'source-bucket'},
        'object': {'key': 'does-not-exist.txt', 'version-id': '123'},
    }

    replicate_object = partition_s3_replicate.ReplicateObject(detail)
    assert 'Item' not in replicate_object.objects_table.get_item(
        Key={'Key': 'does-not-exist.txt', 'VersionId': '123'}
    )

    with pytest.raises(ClientError) as exc_info:
        replicate_object.handle_created()
    assert exc_info.value.response['Error']['Code'] in ('404', 'NotFound', 'AccessDenied')

    # Check that the object is not found
    with pytest.raises(ClientError) as exc_info:
        replicate_object._dst_s3_clnt.head_object(
            Bucket=partition_s3_replicate.DST_BUCKET,
            Key='does-not-exist.txt',
        )
    assert exc_info.value.response['Error']['Code'] in ('404', 'NotFound', 'AccessDenied')

    # Check that no data was recorded in DynamoDB
    assert 'Item' not in replicate_object.objects_table.get_item(
        Key={'Key': 'does-not-exist.txt', 'VersionId': '123'}
    )

@pytest.mark.parametrize('obj_key, obj_ver_idx', [
    pytest.param('foo.txt', 0),
    pytest.param('bar.txt', 0),
    pytest.param('bar.txt', 1),
    pytest.param('baz.txt', 0),
    pytest.param('baz.txt', 1),
    pytest.param('baz.txt', 2),
])
def test_handle_create_dup(setup_s3, setup_s3_destobjs, obj_key, obj_ver_idx):
    obj_data = setup_s3[obj_key][obj_ver_idx]
    obj_ver = obj_data['VersionId']
    detail = {
        'bucket': {'name': 'source-bucket'},
        'object': {'key': obj_key, 'version-id': obj_ver},
    }

    replicate_object = partition_s3_replicate.ReplicateObject(detail)
    assert 'Item' in replicate_object.objects_table.get_item(
        Key={'Key': obj_key, 'VersionId': obj_ver}
    )

    replicate_object.handle_created()

    # Get the object data from the DynamoDB table to check later
    obj_item = replicate_object.objects_table.get_item(
        Key={'Key': obj_key, 'VersionId': obj_ver}
    )['Item']
    assert obj_item
    assert obj_item['DestObject']['VersionId'] == setup_s3_destobjs[obj_key][obj_ver_idx]['VersionId']

    # Make sure no new versions were uploaded
    res = replicate_object._dst_s3_clnt.list_object_versions(
        Bucket=partition_s3_replicate.DST_BUCKET,
        Delimiter='/',
        Prefix=obj_key,
    )
    assert len(res.get('Versions', [])) == len(setup_s3_destobjs[obj_key])

@pytest.mark.parametrize('obj_key, obj_ver_idx', [
    pytest.param('foo.txt', 0),
    pytest.param('bar.txt', 0),
    pytest.param('bar.txt', 1),
    pytest.param('baz.txt', 0),
    pytest.param('baz.txt', 1),
    pytest.param('baz.txt', 2),
])
def test_handle_create_dup_error(monkeypatch, setup_s3, setup_s3_destobjs, obj_key, obj_ver_idx):
    obj_data = setup_s3[obj_key][obj_ver_idx]
    obj_ver = obj_data['VersionId']
    detail = {
        'bucket': {'name': 'source-bucket'},
        'object': {'key': obj_key, 'version-id': obj_ver},
    }
    replicate_object = partition_s3_replicate.ReplicateObject(detail)
    obj_item = replicate_object.objects_table.get_item(
        Key={'Key': obj_key, 'VersionId': obj_ver}
    )['Item']

    # Mocked botocore _make_api_call function
    _make_api_call_orig = botocore.client.BaseClient._make_api_call
    def _make_api_call(self, operation_name, kwarg):
        if operation_name == 'HeadObject':
            bucket = kwarg['Bucket']
            key = kwarg['Key']
            version_id = kwarg.get('VersionId')

            if bucket == partition_s3_replicate.DST_BUCKET and key == obj_key and version_id == obj_item['DestObject']['VersionId']:
                raise ClientError(
                    dict(
                        Error=dict(
                            Code='InternalError',
                            Message='Unknown error occured.',
                        )
                    ),
                    'head_object'
                )
        # If we don't want to patch the API call
        return _make_api_call_orig(self, operation_name, kwarg)
    monkeypatch.setattr(botocore.client.BaseClient, '_make_api_call', _make_api_call)

    replicate_object.handle_created()

    # Get the object data from the DynamoDB table to check later
    obj_item = replicate_object.objects_table.get_item(
        Key={'Key': obj_key, 'VersionId': obj_ver}
    )['Item']
    assert obj_item
    assert obj_item['DestObject']['VersionId'] != setup_s3_destobjs[obj_key][obj_ver_idx]['VersionId']

    # Make sure no new versions were uploaded
    res = replicate_object._dst_s3_clnt.list_object_versions(
        Bucket=partition_s3_replicate.DST_BUCKET,
        Delimiter='/',
        Prefix=obj_key,
    )
    assert len(res.get('Versions', [])) == len(setup_s3_destobjs[obj_key]) + 1

@pytest.mark.parametrize('obj_key, obj_ver_idx', [
    pytest.param('foo.txt', 0),
    pytest.param('bar.txt', 0),
    pytest.param('bar.txt', 1),
    pytest.param('baz.txt', 0),
    pytest.param('baz.txt', 1),
    pytest.param('baz.txt', 2),
])
def test_handle_delete(setup_s3, setup_s3_destobjs, obj_key, obj_ver_idx):
    """ Test handling deletes on a versioned bucket. """
    obj_data = setup_s3[obj_key][obj_ver_idx]
    obj_ver = obj_data['VersionId']
    detail = {
        'bucket': {'name': 'source-bucket'},
        'object': {'key': obj_key, 'version-id': obj_ver},
    }

    replicate_object = partition_s3_replicate.ReplicateObject(detail)
    obj_item = replicate_object.objects_table.get_item(
        Key={'Key': obj_key, 'VersionId': obj_ver}
    )['Item']

    replicate_object.handle_deleted()

    # Verify that the version no longer exists
    with pytest.raises(ClientError) as exc_info:
        replicate_object._dst_s3_clnt.head_object(
            Bucket=partition_s3_replicate.DST_BUCKET,
            Key=obj_key,
            VersionId=obj_item['DestObject']['VersionId']
        )
    assert exc_info.value.response['Error']['Code'] in ('404', 'NotFound')

    # Verify that other versions still exist
    res = replicate_object._dst_s3_clnt.list_object_versions(
        Bucket=partition_s3_replicate.DST_BUCKET,
        Delimiter='/',
        Prefix=obj_key,
    )
    assert len(res.get('Versions', [])) == len(setup_s3[obj_key]) - 1

    # Check that it was deleted from DynamoDB
    assert 'Item' not in replicate_object.objects_table.get_item(
        Key={'Key': obj_key, 'VersionId': obj_ver}
    )

@pytest.mark.parametrize('setup_s3', [
    pytest.param({'versioning': False}, id='nonversioned')
], indirect=True)
@pytest.mark.parametrize('obj_key', [
    pytest.param('foo.txt'),
    pytest.param('bar.txt'),
    pytest.param('baz.txt'),
])
def test_handle_delete_nonversioned(setup_s3, setup_s3_destobjs, obj_key):
    """ Test replicating deletes on a non-versioned bucket. """
    detail = {
        'bucket': {'name': 'source-bucket'},
        'object': {'key': obj_key},
    }

    replicate_object = partition_s3_replicate.ReplicateObject(detail)
    assert 'Item' in replicate_object.objects_table.get_item(
        Key={'Key': obj_key, 'VersionId': '$null'}
    )

    replicate_object.handle_deleted()

    with pytest.raises(ClientError) as exc_info:
        replicate_object._dst_s3_clnt.head_object(
            Bucket=partition_s3_replicate.DST_BUCKET,
            Key=obj_key,
        )
    assert exc_info.value.response['Error']['Code'] in ('404', 'NotFound')

    assert 'Item' not in replicate_object.objects_table.get_item(
        Key={'Key': obj_key, 'VersionId': '$null'}
    )

@pytest.mark.parametrize('obj_key, obj_ver_idx', [
    pytest.param('foo.txt', 0),
    pytest.param('bar.txt', 0),
    pytest.param('bar.txt', 1),
    pytest.param('baz.txt', 0),
    pytest.param('baz.txt', 1),
    pytest.param('baz.txt', 2),
])
def test_handle_delete_notfound(setup_s3, setup_s3_destobjs, obj_key, obj_ver_idx):
    """ Test handling deletes on a versioned bucket, when the destination is not found. """
    obj_data = setup_s3[obj_key][obj_ver_idx]
    obj_ver = obj_data['VersionId']
    detail = {
        'bucket': {'name': 'source-bucket'},
        'object': {'key': obj_key, 'version-id': obj_ver},
    }

    replicate_object = partition_s3_replicate.ReplicateObject(detail)
    obj_item = replicate_object.objects_table.get_item(
        Key={'Key': obj_key, 'VersionId': obj_ver}
    )['Item']

    # Delete the object before calling the API
    replicate_object._dst_s3_clnt.delete_object(
        Bucket=partition_s3_replicate.DST_BUCKET,
        Key=obj_key,
        VersionId=obj_item['DestObject']['VersionId']
    )

    replicate_object.handle_deleted()

    # Verify that other versions still exist
    res = replicate_object._dst_s3_clnt.list_object_versions(
        Bucket=partition_s3_replicate.DST_BUCKET,
        Delimiter='/',
        Prefix=obj_key,
    )
    assert len(res.get('Versions', [])) == len(setup_s3[obj_key]) - 1

    # Check that it was deleted from DynamoDB
    assert 'Item' not in replicate_object.objects_table.get_item(
        Key={'Key': obj_key, 'VersionId': obj_ver}
    )


@pytest.mark.parametrize('obj_key, obj_ver_idx, tags', [
    pytest.param('foo.txt', 0, {'New': '1'}),
    pytest.param('bar.txt', 0, {}),
    pytest.param('bar.txt', 1, {'A': '1', 'B': '2'}),
])
def test_handle_tags(setup_s3, setup_s3_destobjs, obj_key, obj_ver_idx, tags):
    obj_data = setup_s3[obj_key][obj_ver_idx]
    obj_ver = obj_data['VersionId']
    dst_obj_ver = setup_s3_destobjs[obj_key][obj_ver_idx]['VersionId']
    detail = {
        'bucket': {'name': 'source-bucket'},
        'object': {'key': obj_key, 'version-id': obj_ver},
    }

    replicate_object = partition_s3_replicate.ReplicateObject(detail)
    assert 'Item' in replicate_object.objects_table.get_item(
        Key={'Key': obj_key, 'VersionId': obj_ver}
    )

    if tags:
        replicate_object._src_s3_clnt.put_object_tagging(
            Bucket='source-bucket',
            Key=obj_key,
            VersionId=obj_ver,
            Tagging={
                'TagSet': [{'Key': k, 'Value': v} for k, v in tags.items()]
            }
        )
    else:
        replicate_object._src_s3_clnt.delete_object_tagging(
            Bucket='source-bucket',
            Key=obj_key,
            VersionId=obj_ver,
        )

    replicate_object.handle_tags()


    # Get the object data and its tags to check later
    res = replicate_object._dst_s3_clnt.get_object_tagging(
        Bucket=partition_s3_replicate.DST_BUCKET,
        Key=obj_key,
        VersionId=dst_obj_ver,
    )
    dst_object_tags = {t['Key']: t['Value'] for t in res.get('TagSet', [])}

    # Get the object data from the DynamoDB table to check later
    obj_item = replicate_object.objects_table.get_item(
        Key={'Key': obj_key, 'VersionId': obj_ver}
    )['Item']

    assert dst_object_tags == tags
    assert obj_item['DestObject']
    assert obj_item['DestObjectTags'] == tags

@pytest.mark.parametrize('setup_s3', [
    pytest.param({'versioning': False}, id='nonversioned')
], indirect=True)
@pytest.mark.parametrize('obj_key, tags', [
    pytest.param('foo.txt', {'New': '1'}),
    pytest.param('bar.txt', {}),
    pytest.param('bar.txt', {'A': '1', 'B': '2'}),
])
def test_handle_tags_nonversioned(setup_s3, setup_s3_destobjs, obj_key, tags):
    detail = {
        'bucket': {'name': 'source-bucket'},
        'object': {'key': obj_key},
    }

    replicate_object = partition_s3_replicate.ReplicateObject(detail)
    assert 'Item' in replicate_object.objects_table.get_item(
        Key={'Key': obj_key, 'VersionId': '$null'}
    )

    if tags:
        replicate_object._src_s3_clnt.put_object_tagging(
            Bucket='source-bucket',
            Key=obj_key,
            Tagging={
                'TagSet': [{'Key': k, 'Value': v} for k, v in tags.items()]
            }
        )
    else:
        replicate_object._src_s3_clnt.delete_object_tagging(
            Bucket='source-bucket',
            Key=obj_key,
        )

    replicate_object.handle_tags()


    # Get the object data and its tags to check later
    res = replicate_object._dst_s3_clnt.get_object_tagging(
        Bucket=partition_s3_replicate.DST_BUCKET,
        Key=obj_key,
    )
    dst_object_tags = {t['Key']: t['Value'] for t in res.get('TagSet', [])}

    # Get the object data from the DynamoDB table to check later
    obj_item = replicate_object.objects_table.get_item(
        Key={'Key': obj_key, 'VersionId': '$null'}
    )['Item']

    assert dst_object_tags == tags
    assert obj_item['DestObject']
    assert obj_item['DestObjectTags'] == tags