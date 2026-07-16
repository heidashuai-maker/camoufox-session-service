from __future__ import annotations

import time
from dataclasses import dataclass

from .browser import (
    context_options,
    cookies_from_context,
    is_browser_crash_error,
    page_user_agent,
    response_status,
)
from .models import ChallengeRequest, ErrorInfo, TaskResult


@dataclass(frozen=True, slots=True)
class ChallengeEvidence:
    detected: bool
    vendor: str | None = None
    interactive: bool = False


def detect_challenge(page) -> ChallengeEvidence:
    title = (page.title() or "").lower()
    body = (page.content() or "").lower()
    frame_urls = " ".join(str(frame.url or "").lower() for frame in page.frames)
    markers = (
        "just a moment" in title,
        "challenge-form" in body,
        "cf-chl-" in body,
        "challenges.cloudflare.com" in frame_urls,
        "/cdn-cgi/challenge-platform/" in frame_urls,
    )
    detected = any(markers)
    interactive = detected and any(
        marker in body
        for marker in ("verify you are human", "cf-turnstile", "challenge-stage")
    )
    return ChallengeEvidence(
        detected=detected,
        vendor="cloudflare" if detected else None,
        interactive=interactive,
    )


def solve_challenge(browser, request: ChallengeRequest) -> TaskResult:
    started = time.monotonic()
    context = None
    try:
        context = browser.new_context(**context_options(request))
        page = context.new_page()
        response = page.goto(
            str(request.url),
            wait_until="domcontentloaded",
            timeout=request.timeoutMs,
        )
        evidence = detect_challenge(page)
        challenge_was_present = evidence.detected
        deadline = time.monotonic() + request.waitSeconds
        while evidence.detected and time.monotonic() < deadline:
            page.wait_for_timeout(500)
            evidence = detect_challenge(page)

        if not evidence.detected:
            status = "solved" if challenge_was_present else "no_challenge"
        elif evidence.interactive:
            status = "interactive_required"
        else:
            status = "challenge_present"

        return TaskResult(
            status=status,
            finalUrl=str(page.url),
            httpStatus=response_status(response),
            cookies=cookies_from_context(context),
            userAgent=page_user_agent(page),
            html=page.content() if request.returnHtml else None,
            elapsedMs=int((time.monotonic() - started) * 1000),
        )
    except Exception as exc:
        message = str(exc)
        timed_out = "timeout" in message.lower()
        browser_crashed = is_browser_crash_error(exc)
        if browser_crashed:
            status = "browser_crashed"
            error_type = "BROWSER_CRASH"
        elif timed_out:
            status = "timeout"
            error_type = "CHALLENGE_TIMEOUT"
        else:
            status = "failed"
            error_type = "CHALLENGE_FAILED"
        return TaskResult(
            status=status,
            elapsedMs=int((time.monotonic() - started) * 1000),
            error=ErrorInfo(
                type=error_type,
                message=message,
                retryable=True,
                stage="challenge",
            ),
        )
    finally:
        if context is not None:
            context.close()
