import json
import os
from contextlib import contextmanager
from time import sleep
from typing import Generator

import pytest
import requests
from lightning.app.testing.config import Config
from lightning.app.utilities.imports import _is_playwright_available, requires
from lightning_app.utilities.cloud import _get_project
from lightning_app.utilities.network import LightningClient
from lightning_cloud.openapi.rest import ApiException

if _is_playwright_available():
    import playwright
    from playwright.sync_api import sync_playwright


@requires("playwright")
@contextmanager
def get_gallery_app_page(app_name) -> Generator:
    with sync_playwright() as p:
        browser = p.chromium.launch(
            timeout=5000, headless=bool(int(os.getenv("HEADLESS", "0")))
        )
        payload = {
            "apiKey": Config.api_key,
            "username": Config.username,
            "duration": "120000",
        }
        context = browser.new_context(
            record_video_dir=os.path.join(Config.video_location, app_name),
            record_har_path=Config.har_location,
        )
        gallery_page = context.new_page()
        res = requests.post(Config.url + "/v1/auth/login", data=json.dumps(payload))
        token = res.json()["token"]
        gallery_page.goto(Config.url)
        gallery_page.evaluate(
            """data => {
            window.localStorage.setItem('gridUserId', data[0]);
            window.localStorage.setItem('gridUserKey', data[1]);
            window.localStorage.setItem('gridUserToken', data[2]);
        }
        """,
            [Config.id, Config.key, token],
        )
        gallery_page.goto(f"{Config.url}/apps")

        # Find the app in the gallery
        gallery_page.locator(f"text={app_name}").first.click()
        yield gallery_page


@requires("playwright")
@contextmanager
def launch_from_gallery_app_page(gallery_page) -> Generator:
    with gallery_page.context.expect_page() as page_catcher:
        gallery_page.locator("text=Launch").click()

    app_page = page_catcher.value
    app_page.wait_for_load_state(timeout=0)

    try:
        yield app_page
    except KeyboardInterrupt:
        pass


@requires("playwright")
@contextmanager
def clone_and_run_from_gallery_app_page(app_gallery_page) -> Generator:

    with app_gallery_page.expect_navigation():
        app_gallery_page.locator("text=Clone & Run").click()

    admin_page = app_gallery_page

    sleep(5)
    # Scroll to the bottom of the page. Used to capture all logs.
    admin_page.evaluate(
        """
            var intervalID = setInterval(function () {
                var scrollingElement = (document.scrollingElement || document.body);
                scrollingElement.scrollTop = scrollingElement.scrollHeight;
            }, 200);

            if (!window._logs) {
                window._logs = [];
            }

            if (window.logTerminals) {
                Object.entries(window.logTerminals).forEach(
                    ([key, value]) => {
                        window.logTerminals[key]._onLightningWritelnHandler = function (data) {
                            window._logs = window._logs.concat([data]);
                        }
                    }
                );
            }
            """
    )

    # TODO: Add a timeout here.
    while True:
        try:
            open_app_button = admin_page.locator("text=Open App")
            open_app_button.wait_for(timeout=1000)

            if open_app_button.is_disabled():
                sleep(5)
                continue

            with admin_page.context.expect_page() as page_catcher:
                open_app_button.click()
            app_page = page_catcher.value
            app_page.wait_for_load_state(timeout=0)
            break
        except (
            playwright._impl._api_types.Error,
            playwright._impl._api_types.TimeoutError,
        ):
            pass

    def fetch_logs() -> str:
        return admin_page.evaluate("window._logs;")

    lightning_app_id = str(app_page.url).split(".")[0].split("//")[-1]
    print(f"The Lightning Id Name : [bold magenta]{lightning_app_id}[/bold magenta]")

    try:
        yield admin_page, app_page, fetch_logs
    except KeyboardInterrupt:
        pass
    finally:
        print(f"##################### DELETING APP {lightning_app_id}")
        printed_logs = []
        for log in fetch_logs():
            if log not in printed_logs:
                printed_logs.append(log)
                print(log.split("[0m")[-1])
        stop_button = admin_page.locator("text=Stop")
        try:
            stop_button.wait_for(timeout=3 * 1000)
            stop_button.click()
        except (
            playwright._impl._api_types.Error,
            playwright._impl._api_types.TimeoutError,
        ):
            pass

        client = LightningClient()
        project = _get_project(client)
        try:
            res = client.lightningapp_instance_service_delete_lightningapp_instance(
                project_id=project.project_id,
                id=lightning_app_id,
            )
            assert res == {}
        except ApiException as e:
            print(f"Failed to delete app {lightning_app_id}. Exception {e}")


def validate_app_functionalities(app_page: "Page") -> None:
    """
    app_page: The UI page of the app to be validated.
    """

    while True:
        try:
            app_page.reload()
            sleep(5)
            input_label = app_page.frame_locator("iframe").locator(
                "text=Enter your name"
            )
            input_label.wait_for(timeout=30 * 1000)
            break
        except (
            playwright._impl._api_types.Error,
            playwright._impl._api_types.TimeoutError,
        ):
            pass

    create_button = app_page.frame_locator("iframe").locator('button:has-text("Create Jupyter Notebook")')
    create_button.wait_for(timeout=5 * 1000)
    create_button.click()

    sleep(2)
    app_page.reload()
    sleep(5)
    jupyters = app_page.locator("button:has-text('JUPYTERLAB')")

    # Jupyter Notebook is created.
    assert jupyters.count() == 1


# TODO: when the launch button works with the app.
# def test_launch_app_from_gallery():
#     app_name = os.getenv("TEST_APP_NAME", None)
#     if app_name is None:
#         raise ValueError("TEST_APP_NAME environment variable is not set")
#
#     with get_gallery_app_page(app_name) as gallery_page:
#         with launch_from_gallery_app_page(gallery_page) as app_page:
#             validate_app_functionalities(app_page)


@pytest.mark.skipif(
    not os.getenv("TEST_APP_NAME", None), reason="requires TEST_APP_NAME env var"
)
def test_clone_and_run_app_from_gallery():
    app_name = os.getenv("TEST_APP_NAME", None)
    if app_name is None:
        raise ValueError("TEST_APP_NAME environment variable is not set")

    with get_gallery_app_page(app_name) as gallery_page:
        with clone_and_run_from_gallery_app_page(gallery_page) as (_, app_page, _):
            validate_app_functionalities(app_page)
