"""Package entry points preserve lazy loading and shared runtime objects."""

import subprocess
import sys
import textwrap


def test_basic_package_import_does_not_load_server_dependencies():
    subprocess.run(
        [
            sys.executable,
            "-c",
            textwrap.dedent("""
            import sys
            import hoshimi

            assert hoshimi.VERSION
            assert dir(hoshimi) == sorted(hoshimi.__all__)
            assert "fastapi" not in sys.modules
            assert "httpx" not in sys.modules
            try:
                hoshimi.unknown_export
            except AttributeError as exc:
                assert str(exc) == "module 'hoshimi' has no attribute 'unknown_export'"
            else:
                raise AssertionError("unknown package attributes must fail")
        """),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def test_embedding_and_provider_exports_share_the_implementation_objects():
    subprocess.run(
        [
            sys.executable,
            "-c",
            textwrap.dedent("""
            from hoshimi.core.login import manager
            from hoshimi.core.providers import factory
            from hoshimi import Services, build_services, create_app
            from hoshimi.api import app
            from hoshimi.core import context, login, providers

            assert Services is context.Services
            assert build_services is context.build_services
            assert create_app is app.create_app
            assert login.LoginManager is manager.LoginManager
            assert login.LoginSession is manager.LoginSession
            assert login.LoginError is manager.LoginError
            assert login.login_providers is manager.login_providers
            assert providers.PROVIDER_CLASSES is factory.PROVIDER_CLASSES
            assert providers.PROVIDER_ORDER is factory.PROVIDER_ORDER
            assert providers.build_providers is factory.build_providers
            assert providers.real_account_count is factory.real_account_count
        """),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
