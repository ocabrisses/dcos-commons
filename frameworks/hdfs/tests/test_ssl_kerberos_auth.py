import logging
import uuid
import pytest

import sdk_auth
import sdk_cmd
import sdk_hosts
import sdk_install
import sdk_marathon
import sdk_security
import sdk_utils

from security import kerberos as krb5
from security import transport_encryption

from tests import auth
from tests import config


log = logging.getLogger(__name__)


pytestmark = pytest.mark.skipif(sdk_utils.is_open_dcos(),
                                reason='Feature only supported in DC/OS EE')


@pytest.fixture(scope='module', autouse=True)
def service_account(configure_security):
    """
    Creates service account and yields the name.
    """
    try:
        name = config.SERVICE_NAME
        sdk_security.create_service_account(
            service_account_name=name, service_account_secret=name)
        # TODO(mh): Fine grained permissions needs to be addressed in DCOS-16475
        sdk_cmd.run_cli(
            "security org groups add_user superusers {name}".format(name=name))
        yield name
    finally:
        sdk_security.delete_service_account(
            service_account_name=name, service_account_secret=name)


@pytest.fixture(scope='module', autouse=True)
def kerberos(configure_security):
    try:
        principals = auth.get_service_principals(config.SERVICE_NAME, sdk_auth.REALM)

        kerberos_env = sdk_auth.KerberosEnvironment()
        kerberos_env.add_principals(principals)
        kerberos_env.finalize()

        yield kerberos_env

    finally:
        kerberos_env.cleanup()


@pytest.fixture(scope='module', autouse=True)
def hdfs_server(kerberos, service_account):
    """
    A pytest fixture that installs a Kerberized HDFS service.

    On teardown, the service is uninstalled.
    """
    service_kerberos_options = {
        "service": {
            "name": config.SERVICE_NAME,
            "service_account": service_account,
            "service_account_secret": service_account,
            "security": {
                "kerberos": {
                    "enabled": True,
                    "kdc": {
                        "hostname": kerberos.get_host(),
                        "port": int(kerberos.get_port())
                    },
                    "realm": kerberos.get_realm(),
                    "keytab_secret": kerberos.get_keytab_path(),
                },
                "transport_encryption": {
                    "enabled": True
                }
            }
        },
        "hdfs": {
            "security_auth_to_local": auth.get_principal_to_user_mapping()
        }
    }

    sdk_install.uninstall(config.PACKAGE_NAME, config.SERVICE_NAME)
    try:
        sdk_install.install(
            config.PACKAGE_NAME,
            config.SERVICE_NAME,
            config.DEFAULT_TASK_COUNT,
            additional_options=service_kerberos_options,
            timeout_seconds=30 * 60)

        yield {**service_kerberos_options, **{"package_name": config.PACKAGE_NAME}}
    finally:
        sdk_install.uninstall(config.PACKAGE_NAME, config.SERVICE_NAME)


@pytest.fixture(scope='module', autouse=True)
def hdfs_client(kerberos, hdfs_server):
    try:
        client_id = "hdfs-client"
        client = {
            "id": client_id,
            "mem": 1024,
            "user": "nobody",
            "container": {
                "type": "MESOS",
                "docker": {
                    "image": "elezar/hdfs-client:dev",
                    "forcePullImage": True
                },
                "volumes": [
                    {
                        "containerPath": "/hadoop-2.6.0-cdh5.9.1/hdfs.keytab",
                        "secret": "hdfs_keytab"
                    }
                ]
            },
            "secrets": {
                "hdfs_keytab": {
                    "source": kerberos.get_keytab_path()
                }
            },
            "networks": [
                {
                    "mode": "host"
                }
            ],
            "env": {
                "REALM": kerberos.get_realm(),
                "KDC_ADDRESS": kerberos.get_kdc_address(),
                "JAVA_HOME": "/usr/lib/jvm/default-java",
                "KRB5_CONFIG": "/etc/krb5.conf",
                "HDFS_SERVICE_NAME": config.SERVICE_NAME,
            }
        }

        sdk_marathon.install_app(client)

        krb5.write_krb5_config_file(client_id, "/etc/krb5.conf", kerberos)
        dcos_ca_bundle = transport_encryption.fetch_dcos_ca_bundle(client_id)

        yield {**client, **{"dcos_ca_bundle": dcos_ca_bundle}}

    finally:
        sdk_marathon.destroy_app(client_id)


# TODO(elezar) Is there a better way to determine this?
DEFAULT_JOURNAL_NODE_TLS_PORT = 8481
DEFAULT_NAME_NODE_TLS_PORT = 9003
DEFAULT_DATA_NODE_TLS_PORT = 9006


@pytest.mark.tls
@pytest.mark.sanity
@pytest.mark.dcos_min_version('1.10')
@sdk_utils.dcos_ee_only
@pytest.mark.parametrize("node_type,port", [
    ('journal', DEFAULT_JOURNAL_NODE_TLS_PORT),
    ('name', DEFAULT_NAME_NODE_TLS_PORT),
    ('data', DEFAULT_DATA_NODE_TLS_PORT),
])
def test_verify_https_ports(hdfs_client, node_type, port):
    """
    Verify that HTTPS port is open name, journal and data node types.
    """

    task_id = "{}-0-node".format(node_type)
    host = sdk_hosts.autoip_host(
        config.SERVICE_NAME, task_id, port)

    cmd = ["curl", "-v",
           "--cacert", hdfs_client["dcos_ca_bundle"],
           "https://{host}".format(host=host), ]

    rc, stdout, stderr = sdk_cmd.task_exec(hdfs_client["id"], " ".join(cmd))
    assert not rc

    assert "SSL connection using TLS1.2 / ECDHE_RSA_AES_128_GCM_SHA256" in stderr
    assert "server certificate verification OK" in stderr
    assert "common name: {}.{} (matched)".format(task_id, config.SERVICE_NAME) in stderr

    # In the Kerberos case we expect a 401 error
    assert "401 Authentication required" in stdout


@pytest.mark.dcos_min_version('1.10')
@sdk_utils.dcos_ee_only
@pytest.mark.auth
@pytest.mark.sanity
def test_user_can_auth_and_write_and_read(hdfs_client, kerberos):
    sdk_auth.kinit(hdfs_client["id"], keytab=config.KEYTAB, principal=kerberos.get_principal("hdfs"))

    test_filename = "test_auth_write_read-{}".format(str(uuid.uuid4()))
    write_cmd = "/bin/bash -c '{}'".format(config.hdfs_write_command(config.TEST_CONTENT_SMALL, test_filename))
    sdk_cmd.task_exec(hdfs_client["id"], write_cmd)

    read_cmd = "/bin/bash -c '{}'".format(config.hdfs_read_command(test_filename))
    _, stdout, _ = sdk_cmd.task_exec(hdfs_client["id"], read_cmd)
    assert stdout == config.TEST_CONTENT_SMALL


@pytest.mark.dcos_min_version('1.10')
@sdk_utils.dcos_ee_only
@pytest.mark.auth
@pytest.mark.sanity
def test_users_have_appropriate_permissions(hdfs_client, kerberos):
    # "hdfs" is a superuser

    sdk_auth.kinit(hdfs_client["id"], keytab=config.KEYTAB, principal=kerberos.get_principal("hdfs"))

    log.info("Creating directory for alice")
    make_user_directory_cmd = config.hdfs_command("mkdir -p /users/alice")
    sdk_cmd.task_exec(hdfs_client["id"], make_user_directory_cmd)

    change_ownership_cmd = config.hdfs_command("chown alice:users /users/alice")
    sdk_cmd.task_exec(hdfs_client["id"], change_ownership_cmd)

    change_permissions_cmd = config.hdfs_command("chmod 700 /users/alice")
    sdk_cmd.task_exec(hdfs_client["id"], change_permissions_cmd)

    test_filename = "test_user_permissions-{}".format(str(uuid.uuid4()))

    # alice has read/write access to her directory
    sdk_auth.kdestroy(hdfs_client["id"])
    sdk_auth.kinit(hdfs_client["id"], keytab=config.KEYTAB, principal=kerberos.get_principal("alice"))
    write_access_cmd = "/bin/bash -c \"{}\"".format(config.hdfs_write_command(
        config.TEST_CONTENT_SMALL,
        "/users/alice/{}".format(test_filename)))
    log.info("Alice can write: %s", write_access_cmd)
    rc, stdout, _ = sdk_cmd.task_exec(hdfs_client["id"], write_access_cmd)
    assert stdout == '' and rc == 0

    read_access_cmd = config.hdfs_read_command("/users/alice/{}".format(test_filename))
    log.info("Alice can read: %s", read_access_cmd)
    _, stdout, _ = sdk_cmd.task_exec(hdfs_client["id"], read_access_cmd)
    assert stdout == config.TEST_CONTENT_SMALL

    ls_cmd = config.hdfs_command("ls /users/alice")
    _, stdout, _ = sdk_cmd.task_exec(hdfs_client["id"], ls_cmd)
    assert "/users/alice/{}".format(test_filename) in stdout

    # bob doesn't have read/write access to alice's directory
    sdk_auth.kdestroy(hdfs_client["id"])
    sdk_auth.kinit(hdfs_client["id"], keytab=config.KEYTAB, principal=kerberos.get_principal("bob"))

    log.info("Bob tries to wrtie to alice's directory: %s", write_access_cmd)
    _, _, stderr = sdk_cmd.task_exec(hdfs_client["id"], write_access_cmd)
    log.info("Bob can't write to alice's directory: %s", write_access_cmd)
    assert "put: Permission denied: user=bob" in stderr

    log.info("Bob tries to read from alice's directory: %s", read_access_cmd)
    _, _, stderr = sdk_cmd.task_exec(hdfs_client["id"], read_access_cmd)
    log.info("Bob can't read from alice's directory: %s", read_access_cmd)
    assert "cat: Permission denied: user=bob" in stderr
