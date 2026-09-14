from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Callable

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright

from .constants import LOGIN_URL


class BrowserSession:
    """Browser session used by the wrapper.

    ``mode='native'`` launches the user's installed Chrome/Edge as a normal
    standalone process with a dedicated Cineworld profile, then attaches over
    Chrome DevTools Protocol. This keeps the login/challenge interaction in a
    normal visible browser rather than a Playwright-launched automation browser.

    ``mode='playwright'`` retains the old direct Playwright launch as a fallback.
    """

    def __init__(
        self,
        profile_dir: Path,
        *,
        headless: bool = False,
        browser_channel: str | None = "auto",
        mode: str = "native",
    ):
        self.profile_dir = profile_dir
        self.headless = headless
        self.browser_channel = browser_channel
        self.mode = mode

        self.playwright: Playwright | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None

        self._native_process: subprocess.Popen | None = None
        self._native_port: int | None = None

    def start(self, response_handler: Callable | None = None) -> Page:
        if self.context is not None and self.page is not None:
            return self.page

        self.profile_dir.mkdir(parents=True, exist_ok=True)

        if self.mode == "native" and not self.headless:
            try:
                return self._start_native(response_handler)
            except Exception as exc:
                print(
                    "Native Chrome attach failed; falling back to Playwright "
                    f"browser mode. Reason: {exc}"
                )

        return self._start_playwright(response_handler)

    def _start_native(self, response_handler: Callable | None) -> Page:
        executable = self._find_native_browser()
        if executable is None:
            raise RuntimeError("Could not find installed Chrome or Edge")

        port = self._free_local_port()
        self._native_port = port

        args = [
            str(executable),
            f"--remote-debugging-port={port}",
            f"--user-data-dir={self.profile_dir.resolve()}",
            "--no-first-run",
            "--no-default-browser-check",
            LOGIN_URL,
        ]

        creationflags = 0
        if os.name == "nt" and hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

        self._native_process = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )

        endpoint = f"http://127.0.0.1:{port}"
        self._wait_for_cdp(endpoint)

        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.connect_over_cdp(endpoint)

        if not self.browser.contexts:
            raise RuntimeError("Chrome started but exposed no browser context")

        self.context = self.browser.contexts[0]
        if response_handler is not None:
            self.context.on("response", response_handler)

        pages = self.context.pages
        self.page = pages[0] if pages else self.context.new_page()
        return self.page

    @staticmethod
    def _free_local_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    @staticmethod
    def _wait_for_cdp(endpoint: str, timeout: float = 15.0) -> None:
        deadline = time.monotonic() + timeout
        url = endpoint + "/json/version"
        last_error: Exception | None = None

        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=1.0) as response:
                    if response.status == 200:
                        return
            except Exception as exc:
                last_error = exc
                time.sleep(0.2)

        raise RuntimeError(f"Chrome DevTools endpoint did not start: {last_error}")

    def _find_native_browser(self) -> Path | None:
        candidates: list[str | Path] = []

        explicit = os.environ.get("CINEWORLD_BROWSER_EXE")
        if explicit:
            candidates.append(explicit)

        if os.name == "nt":
            for root_name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
                root = os.environ.get(root_name)
                if not root:
                    continue
                candidates.extend(
                    [
                        Path(root) / "Google/Chrome/Application/chrome.exe",
                        Path(root) / "Microsoft/Edge/Application/msedge.exe",
                    ]
                )
        elif sys_platform() == "darwin":
            candidates.extend(
                [
                    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                ]
            )
        else:
            candidates.extend(
                filter(
                    None,
                    [
                        shutil.which("google-chrome"),
                        shutil.which("google-chrome-stable"),
                        shutil.which("chromium"),
                        shutil.which("chromium-browser"),
                        shutil.which("microsoft-edge"),
                    ],
                )
            )

        for candidate in candidates:
            path = Path(candidate)
            if path.is_file():
                return path
        return None

    def _start_playwright(self, response_handler: Callable | None) -> Page:
        self.playwright = sync_playwright().start()

        channels: list[str | None]
        if self.browser_channel == "auto":
            channels = ["chrome", "msedge", None]
        else:
            channels = [self.browser_channel]

        errors: list[str] = []
        for channel in channels:
            try:
                kwargs = {
                    "user_data_dir": str(self.profile_dir.resolve()),
                    "headless": self.headless,
                    "viewport": None,
                }
                if channel:
                    kwargs["channel"] = channel
                self.context = self.playwright.chromium.launch_persistent_context(**kwargs)
                break
            except Exception as exc:
                errors.append(f"{channel or 'chromium'}: {exc}")

        if self.context is None:
            self.playwright.stop()
            self.playwright = None
            raise RuntimeError(
                "Could not launch Chrome, Edge, or Playwright Chromium.\n"
                + "\n".join(errors)
                + "\nTry: python -m playwright install chromium"
            )

        if response_handler is not None:
            self.context.on("response", response_handler)

        self.page = self.context.new_page()
        return self.page

    def open_login(self) -> Page:
        if self.page is None:
            raise RuntimeError("Browser has not been started")
        if not self.page.url.startswith("https://experience.cineworld.co.uk/login"):
            self.page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60_000)
        else:
            self.page.wait_for_load_state("domcontentloaded", timeout=60_000)
        return self.page

    def fill_login(self, email: str, password: str) -> bool:
        if self.page is None:
            raise RuntimeError("Browser has not been started")

        email_input = self._first_visible([
            'input[type="email"]',
            'input[autocomplete="email"]',
            'input[autocomplete="username"]',
            'input[name*="email" i]',
            'input[id*="email" i]',
        ])
        password_input = self._first_visible([
            'input[type="password"]',
            'input[autocomplete="current-password"]',
            'input[name*="password" i]',
            'input[id*="password" i]',
        ])

        if email_input is None or password_input is None:
            return False

        email_input.fill(email)
        password_input.fill(password)
        return True

    def submit_login_form(self) -> bool:
        if self.page is None:
            raise RuntimeError("Browser has not been started")

        password = self._first_visible([
            'input[type="password"]',
            'input[autocomplete="current-password"]',
        ])
        if password is None:
            return False

        try:
            form = password.locator("xpath=ancestor::form[1]")
            if form.count():
                submit = form.locator('button[type="submit"], input[type="submit"]')
                for index in range(submit.count()):
                    candidate = submit.nth(index)
                    if candidate.is_visible() and candidate.is_enabled():
                        candidate.click()
                        return True

                for pattern in (r"^log\s*in$", r"^login$", r"^sign\s*in$"):
                    button = form.get_by_role("button", name=re.compile(pattern, re.I))
                    if button.count() and button.first.is_visible() and button.first.is_enabled():
                        button.first.click()
                        return True
        except Exception:
            return False
        return False

    def _first_visible(self, selectors: list[str]):
        assert self.page is not None
        for selector in selectors:
            try:
                locator = self.page.locator(selector)
                for index in range(locator.count()):
                    candidate = locator.nth(index)
                    if candidate.is_visible():
                        return candidate
            except Exception:
                continue
        return None

    def user_agent(self) -> str:
        if self.page is None:
            raise RuntimeError("Browser has not been started")
        return self.page.evaluate("() => navigator.userAgent")

    def close(self) -> None:
        if self.browser is not None:
            try:
                self.browser.close()
            except Exception:
                pass
            self.browser = None

        if self.context is not None:
            try:
                self.context.close()
            except Exception:
                pass
            self.context = None

        self.page = None

        if self.playwright is not None:
            try:
                self.playwright.stop()
            except Exception:
                pass
            self.playwright = None

        if self._native_process is not None:
            try:
                if self._native_process.poll() is None:
                    self._native_process.terminate()
                    self._native_process.wait(timeout=3)
            except Exception:
                try:
                    self._native_process.kill()
                except Exception:
                    pass
            self._native_process = None


def sys_platform() -> str:
    import sys

    return sys.platform
