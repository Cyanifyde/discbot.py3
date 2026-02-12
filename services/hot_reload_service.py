"""
Git-based hot reload service.

Polls `origin/main` and immediately applies updates by pulling and reloading
runtime modules/services when a new commit is detected.
"""
from __future__ import annotations

import asyncio
import importlib
import logging
import os
import py_compile
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Optional

from core.command_registry import command_registry
from core.help_system import help_system
from core.interactions import list_component_prefixes, unregister_component_handler
from core.paths import BASE_DIR
from core.utils import utcnow
from modules.verification import VERIFY_BUTTON_PREFIX
from services.sync_service import (
    SYNC_APPROVAL_BUTTON_PREFIX,
    SYNC_PROTECTION_BUTTON_PREFIX,
)

if TYPE_CHECKING:
    from bot.client import DiscBot

logger = logging.getLogger("discbot.hot_reload")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except Exception:
        return default


def _normalize_paths(raw_paths: str) -> set[str]:
    out: set[str] = set()
    for token in (raw_paths or "").split(","):
        value = token.strip().replace("\\", "/")
        if value:
            out.add(value)
    return out


async def _maybe_await(result: Any) -> Any:
    if asyncio.iscoroutine(result):
        return await result
    return result


@dataclass
class ReloadUnit:
    module_name: str
    command_routes: list[str] = field(default_factory=list)
    help_modules: list[str] = field(default_factory=list)
    interaction_prefixes: list[str] = field(default_factory=list)
    setup_calls: list[str] = field(default_factory=list)
    restore_calls: list[str] = field(default_factory=list)
    cleanup_call: Optional[str] = None
    post_reload_calls: list[str] = field(default_factory=list)
    hook_bindings: dict[str, str] = field(default_factory=dict)


RELOAD_UNITS: list[ReloadUnit] = [
    ReloadUnit(
        module_name="modules.verification",
        command_routes=["verification", "verification_remove"],
        help_modules=["Verification"],
        interaction_prefixes=[VERIFY_BUTTON_PREFIX],
        setup_calls=["setup_verification"],
        restore_calls=["restore_verification_views"],
    ),
    ReloadUnit(
        module_name="modules.moderation",
        command_routes=["moderation"],
        help_modules=["Moderation"],
        setup_calls=["setup_moderation"],
    ),
    ReloadUnit(
        module_name="modules.server_stats",
        command_routes=["server_stats"],
        help_modules=["Server Stats"],
        setup_calls=["setup_server_stats"],
    ),
    ReloadUnit(
        module_name="modules.server_link",
        command_routes=["server_link"],
        help_modules=["Server Link"],
        setup_calls=["setup_server_link"],
    ),
    ReloadUnit(
        module_name="modules.reports",
        command_routes=["reports"],
        help_modules=["Reports"],
        setup_calls=["setup_reports"],
    ),
    ReloadUnit(
        module_name="modules.utility",
        command_routes=["utility"],
        help_modules=["Utility", "Bookmarks", "AFK", "Notes", "Aliases", "Export"],
        setup_calls=["setup_utility"],
        hook_bindings={"bookmark_reaction": "handle_bookmark_reaction"},
    ),
    ReloadUnit(
        module_name="modules.communication",
        command_routes=["communication"],
        help_modules=["Communication", "Feedback", "Notify", "Acknowledgments"],
        setup_calls=["setup_communication"],
    ),
    ReloadUnit(
        module_name="modules.art_tools",
        command_routes=["art_tools"],
        help_modules=["Art Tools", "Palette"],
        setup_calls=["setup_art_tools"],
    ),
    ReloadUnit(
        module_name="modules.art_search",
        command_routes=["art_search"],
        help_modules=["Art Search"],
        setup_calls=["setup_art_search"],
    ),
    ReloadUnit(
        module_name="modules.automation",
        command_routes=["automation"],
        help_modules=["Automation", "Triggers", "Schedules", "Vacation Mode"],
        setup_calls=["setup_automation"],
    ),
    ReloadUnit(
        module_name="modules.roles",
        command_routes=["roles"],
        help_modules=[
            "Roles",
            "Temp Roles",
            "Role Requests",
            "Approve Role Requests",
            "Role Bundles",
            "Reaction Roles",
        ],
        setup_calls=["setup_roles"],
        restore_calls=["restore_reaction_roles"],
        hook_bindings={"reaction_role_event": "handle_reaction_role_event"},
    ),
    ReloadUnit(
        module_name="modules.custom_content",
        command_routes=["custom_content", "custom_content_fallback"],
        help_modules=["Custom Content"],
        setup_calls=["setup_custom_content"],
    ),
    ReloadUnit(
        module_name="modules.invite_protection",
        help_modules=["Invite Protection"],
        setup_calls=["setup_invite_protection"],
        hook_bindings={"invite_protection": "handle_invite_protection"},
    ),
    ReloadUnit(
        module_name="modules.modules_command",
        command_routes=["modules_command"],
        help_modules=["Module Management"],
        setup_calls=["register_help"],
    ),
    ReloadUnit(
        module_name="modules.auto_responder",
        command_routes=["auto_responder_list", "auto_responder_add", "auto_responder_remove"],
        help_modules=["Auto-Responder"],
        setup_calls=["setup_auto_responder"],
        hook_bindings={"auto_responder": "handle_auto_responder"},
    ),
    ReloadUnit(
        module_name="modules.dm_sender",
        hook_bindings={"dm_send": "handle_dm_send"},
    ),
    ReloadUnit(
        module_name="services.scanner",
        command_routes=["scanner"],
        help_modules=["Scanner"],
        setup_calls=["setup_scanner_service"],
        restore_calls=["restore_state"],
    ),
    ReloadUnit(
        module_name="services.inactivity",
        command_routes=["inactivity"],
        help_modules=["Inactivity Enforcement"],
        setup_calls=["setup_inactivity_service"],
        restore_calls=["restore_state"],
    ),
    ReloadUnit(
        module_name="services.ptc_service",
        help_modules=["Pass the Canvas"],
        setup_calls=["setup_ptc"],
        restore_calls=["restore_ptc_state"],
        cleanup_call="cleanup_ptc",
        hook_bindings={
            "ptc_message": "handle_ptc_message",
            "ptc_reaction": "handle_ptc_reaction",
        },
    ),
    ReloadUnit(
        module_name="services.sync_service",
        interaction_prefixes=[SYNC_APPROVAL_BUTTON_PREFIX, SYNC_PROTECTION_BUTTON_PREFIX],
        setup_calls=["setup_sync_interactions"],
        post_reload_calls=["reset_sync_service"],
    ),
]


class HotReloadService:
    def __init__(self) -> None:
        self.repo_root = Path(BASE_DIR)
        self.enabled = _env_bool("HOT_RELOAD_ENABLED", True)
        self.remote = os.getenv("HOT_RELOAD_REMOTE", "origin").strip() or "origin"
        self.branch = os.getenv("HOT_RELOAD_BRANCH", "main").strip() or "main"
        self.poll_seconds = max(5, _env_int("HOT_RELOAD_POLL_SECONDS", 60))
        self.protected_files = _normalize_paths(
            os.getenv("HOT_RELOAD_PROTECTED_FILES", "main.py,main_runtime.py")
        )
        self.protected_files.update({"main.py", "main_runtime.py"})
        self._protected_log_every_seconds = max(60, self.poll_seconds * 5)
        self._last_protected_log_at: float = 0.0
        self._last_protected_remote_head: Optional[str] = None

        self._bot: Optional["DiscBot"] = None
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._poll_lock = asyncio.Lock()
        self._reload_lock = asyncio.Lock()
        self._paused = False

        self._last_check_at: Optional[str] = None
        self._last_local_head: Optional[str] = None
        self._last_remote_head: Optional[str] = None
        self._last_result: str = "idle"
        self._protected_halt = False

        level_name = os.getenv("HOT_RELOAD_LOG_LEVEL", "INFO").upper()
        level = getattr(logging, level_name, logging.INFO)
        logger.setLevel(level)

    async def start(self, bot: "DiscBot") -> None:
        self._bot = bot
        self._paused = False
        self._stop_event.clear()

        if not self.enabled:
            logger.info("Hot reload disabled by HOT_RELOAD_ENABLED=false")
            return

        if self._task and not self._task.done():
            return

        self._task = asyncio.create_task(self._poll_loop(), name="hot-reload-poller")
        logger.info(
            "Hot reload started (remote=%s branch=%s poll=%ss protected=%s)",
            self.remote,
            self.branch,
            self.poll_seconds,
            ",".join(sorted(self.protected_files)),
        )

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.debug("Hot reload stop error: %s", exc)
        self._task = None

    def pause(self) -> None:
        self._paused = True
        logger.info("Hot reload paused")

    def resume(self) -> None:
        self._paused = False
        logger.info("Hot reload resumed")

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "running": bool(self._task and not self._task.done()),
            "paused": self._paused,
            "remote": self.remote,
            "branch": self.branch,
            "poll_seconds": self.poll_seconds,
            "protected_files": sorted(self.protected_files),
            "protected_halt": self._protected_halt,
            "last_check_at": self._last_check_at,
            "last_local_head": self._last_local_head,
            "last_remote_head": self._last_remote_head,
            "last_result": self._last_result,
        }

    async def check_and_update_once(self) -> dict[str, Any]:
        if self._bot is None:
            return {
                "status": "error",
                "detail": "bot_not_started",
            }

        async with self._poll_lock:
            return await self._check_once(manual=True)

    async def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            if not self._paused:
                try:
                    async with self._poll_lock:
                        await self._check_once(manual=False)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Hot reload poll iteration failed")

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.poll_seconds)
            except asyncio.TimeoutError:
                pass

    async def _check_once(self, *, manual: bool) -> dict[str, Any]:
        self._last_check_at = utcnow().isoformat()
        remote_ref = f"{self.remote}/{self.branch}"

        fetch_ok, _out, fetch_err = await self._run_git("fetch", self.remote, self.branch)
        if not fetch_ok:
            self._last_result = "fetch_failed"
            logger.warning("Hot reload fetch failed: %s", fetch_err.strip() or "<no stderr>")
            return {
                "status": "error",
                "detail": "fetch_failed",
                "error": fetch_err.strip(),
            }

        local_head = await self._git_value("rev-parse", "HEAD")
        remote_head = await self._git_value("rev-parse", remote_ref)
        self._last_local_head = local_head
        self._last_remote_head = remote_head

        if not local_head or not remote_head:
            self._last_result = "head_lookup_failed"
            return {
                "status": "error",
                "detail": "head_lookup_failed",
                "local_head": local_head,
                "remote_head": remote_head,
            }

        if local_head == remote_head:
            self._protected_halt = False
            self._last_result = "no_change"
            return {
                "status": "no_change",
                "local_head": local_head,
                "remote_head": remote_head,
            }

        incoming_files = await self._incoming_files(remote_ref)
        if incoming_files is None:
            self._last_result = "diff_failed"
            return {
                "status": "error",
                "detail": "diff_failed",
                "local_head": local_head,
                "remote_head": remote_head,
            }
        if self._has_protected_changes(incoming_files):
            self._protected_halt = True
            self._last_result = "protected_halt"
            self._log_protected_halt(remote_head, incoming_files)
            return {
                "status": "protected_halt",
                "detail": "protected_file_changed",
                "local_head": local_head,
                "remote_head": remote_head,
                "incoming_files": sorted(incoming_files),
            }

        self._protected_halt = False

        dirty = await self._is_worktree_dirty()
        if dirty:
            logger.warning("Hot reload detected dirty worktree; discarding local changes")
            reset_ok, _reset_out, reset_err = await self._run_git("reset", "--hard", "HEAD")
            if not reset_ok:
                self._last_result = "reset_failed"
                return {
                    "status": "error",
                    "detail": "reset_failed",
                    "error": reset_err.strip(),
                }
            clean_ok, _clean_out, clean_err = await self._run_git("clean", "-fd")
            if not clean_ok:
                self._last_result = "clean_failed"
                return {
                    "status": "error",
                    "detail": "clean_failed",
                    "error": clean_err.strip(),
                }

        pull_ok, _pull_out, pull_err = await self._run_git(
            "pull",
            "--ff-only",
            self.remote,
            self.branch,
        )
        if not pull_ok:
            self._last_result = "pull_failed"
            logger.warning("Hot reload pull failed: %s", pull_err.strip() or "<no stderr>")
            return {
                "status": "error",
                "detail": "pull_failed",
                "error": pull_err.strip(),
                "local_head": local_head,
                "remote_head": remote_head,
            }

        # Refresh local head after pull.
        new_local_head = await self._git_value("rev-parse", "HEAD")
        self._last_local_head = new_local_head

        reload_result = await self._run_full_reload_cycle(incoming_files)
        self._last_result = "updated" if reload_result.get("success") else "reload_failed"

        return {
            "status": "updated" if reload_result.get("success") else "reload_failed",
            "detail": reload_result.get("detail"),
            "local_head": new_local_head,
            "remote_head": remote_head,
            "incoming_files": sorted(incoming_files),
            "reload_success": bool(reload_result.get("success")),
            "reload_summary": reload_result,
            "manual": manual,
        }

    async def _run_full_reload_cycle(self, incoming_files: set[str]) -> dict[str, Any]:
        bot = self._bot
        if bot is None:
            return {"success": False, "detail": "bot_not_started"}

        async with self._reload_lock:
            compile_errors = self._preflight_compile(incoming_files)
            if compile_errors:
                logger.error(
                    "Hot reload preflight failed; aborting reload cycle: %s",
                    "; ".join(compile_errors),
                )
                return {
                    "success": False,
                    "detail": "preflight_failed",
                    "compile_errors": compile_errors,
                }

            old_hooks = bot.get_runtime_hooks_snapshot()
            unit_results: list[dict[str, Any]] = []

            for unit in RELOAD_UNITS:
                unit_result = await self._reload_unit(unit, bot, old_hooks)
                unit_results.append(unit_result)

            routes = command_registry.list_routes()
            component_prefixes = list_component_prefixes()
            success = all(item.get("success", False) for item in unit_results)

            logger.info(
                "Hot reload summary success=%s routes=%d components=%d units=%s",
                success,
                len(routes),
                len(component_prefixes),
                ", ".join(
                    f"{item.get('unit')}={'ok' if item.get('success') else 'fail'}"
                    for item in unit_results
                ),
            )

            return {
                "success": success,
                "detail": "ok" if success else "partial_failure",
                "route_count": len(routes),
                "routes": routes,
                "component_prefixes": component_prefixes,
                "units": unit_results,
            }

    async def _reload_unit(
        self,
        unit: ReloadUnit,
        bot: "DiscBot",
        old_hooks: dict[str, Callable[..., Awaitable[Any]]],
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "unit": unit.module_name,
            "success": True,
            "details": [],
        }

        try:
            removed = command_registry.unregister_many(unit.command_routes)
            if unit.command_routes:
                result["details"].append(f"routes_removed={removed}")

            for module_name in unit.help_modules:
                help_system.unregister_module(module_name)

            for prefix in unit.interaction_prefixes:
                unregister_component_handler(prefix)

            old_module = sys.modules.get(unit.module_name)
            if old_module is not None and unit.cleanup_call:
                cleanup = getattr(old_module, unit.cleanup_call, None)
                if callable(cleanup):
                    await _maybe_await(cleanup())

            importlib.invalidate_caches()
            if old_module is None:
                module = importlib.import_module(unit.module_name)
            else:
                module = importlib.reload(old_module)

            for callable_name in unit.setup_calls:
                await self._invoke_module_callable(module, callable_name)

            for callable_name in unit.restore_calls:
                await self._invoke_module_callable(module, callable_name, bot)

            for callable_name in unit.post_reload_calls:
                await self._invoke_module_callable(module, callable_name)

            self._rebind_runtime_hooks(unit, module, bot, old_hooks)
        except Exception as exc:
            result["success"] = False
            result["error"] = str(exc)
            logger.exception("Hot reload unit failed: %s", unit.module_name)
            # Keep previous hooks when rebind/setup failed for this unit.
            for hook_name in unit.hook_bindings:
                old_hook = old_hooks.get(hook_name)
                if old_hook:
                    bot.set_runtime_hook(hook_name, old_hook)

        return result

    def _rebind_runtime_hooks(
        self,
        unit: ReloadUnit,
        module: Any,
        bot: "DiscBot",
        old_hooks: dict[str, Callable[..., Awaitable[Any]]],
    ) -> None:
        for hook_name, attr_name in unit.hook_bindings.items():
            hook_fn = getattr(module, attr_name, None)
            if callable(hook_fn):
                bot.set_runtime_hook(hook_name, hook_fn)
            else:
                old_hook = old_hooks.get(hook_name)
                if old_hook:
                    bot.set_runtime_hook(hook_name, old_hook)
                raise RuntimeError(
                    f"{unit.module_name} missing hook `{attr_name}` for runtime slot `{hook_name}`"
                )

    async def _invoke_module_callable(self, module: Any, callable_name: str, *args: Any) -> Any:
        fn = getattr(module, callable_name, None)
        if fn is None:
            raise RuntimeError(f"{module.__name__}.{callable_name} not found")
        if not callable(fn):
            raise RuntimeError(f"{module.__name__}.{callable_name} is not callable")
        return await _maybe_await(fn(*args))

    def _preflight_compile(self, incoming_files: set[str]) -> list[str]:
        errors: list[str] = []
        python_files = sorted(path for path in incoming_files if path.endswith(".py"))

        for rel in python_files:
            full = self.repo_root / rel
            if not full.exists():
                continue
            try:
                py_compile.compile(str(full), doraise=True)
            except py_compile.PyCompileError as exc:
                errors.append(f"{rel}: {exc.msg}")
            except Exception as exc:
                errors.append(f"{rel}: {exc}")
        return errors

    async def _incoming_files(self, remote_ref: str) -> Optional[set[str]]:
        ok, stdout, _stderr = await self._run_git("diff", "--name-only", f"HEAD..{remote_ref}")
        if not ok:
            return None
        out: set[str] = set()
        for line in stdout.splitlines():
            value = line.strip().replace("\\", "/")
            if value:
                out.add(value)
        return out

    def _has_protected_changes(self, incoming_files: set[str]) -> bool:
        if not incoming_files:
            return False
        return any(path in self.protected_files for path in incoming_files)

    def _log_protected_halt(self, remote_head: str, incoming_files: set[str]) -> None:
        now = time.monotonic()
        if (
            self._last_protected_remote_head == remote_head
            and now - self._last_protected_log_at < self._protected_log_every_seconds
        ):
            return

        blocked = sorted(path for path in incoming_files if path in self.protected_files)
        logger.warning(
            "Hot reload halted: protected files changed in %s (%s)",
            remote_head,
            ", ".join(blocked),
        )
        self._last_protected_log_at = now
        self._last_protected_remote_head = remote_head

    async def _is_worktree_dirty(self) -> bool:
        ok, stdout, _stderr = await self._run_git("status", "--porcelain")
        if not ok:
            return False
        return bool(stdout.strip())

    async def _git_value(self, *args: str) -> Optional[str]:
        ok, stdout, _stderr = await self._run_git(*args)
        if not ok:
            return None
        value = stdout.strip()
        return value or None

    async def _run_git(self, *args: str) -> tuple[bool, str, str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                *args,
                cwd=str(self.repo_root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_b, stderr_b = await proc.communicate()
        except FileNotFoundError:
            return False, "", "git executable not found"
        except Exception as exc:
            return False, "", str(exc)

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        return proc.returncode == 0, stdout, stderr


_SERVICE = HotReloadService()


async def start(bot: "DiscBot") -> None:
    await _SERVICE.start(bot)


async def stop() -> None:
    await _SERVICE.stop()


def status() -> dict[str, Any]:
    return _SERVICE.status()


def pause() -> None:
    _SERVICE.pause()


def resume() -> None:
    _SERVICE.resume()


async def check_and_update_once() -> dict[str, Any]:
    return await _SERVICE.check_and_update_once()
