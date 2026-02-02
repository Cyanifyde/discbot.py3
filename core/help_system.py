"""
Help system - centralized help registration and display.

Each module can register its help information with the help system,
which then provides a unified help command that displays all available modules.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import discord


@dataclass
class ModuleHelp:
    """Help information for a single module."""
    
    name: str
    description: str
    help_command: str = ""  # Command to run for detailed help (e.g., "scanner help")
    commands: list[tuple[str, str]] = field(default_factory=list)
    group: str = ""  # Optional grouping label (not currently rendered)
    hidden: bool = False  # If True, omit from @bot help overview
    # (command, description) tuples
    
    def to_embed_field(self) -> dict[str, Any]:
        """Convert to Discord embed field format for overview."""
        value_parts = [self.description]
        
        # If module has help_command, show that
        if self.help_command:
            value_parts.append(f"\n**Detailed Help:** `{self.help_command}`")
        # If no help_command but has commands, show them inline
        elif self.commands:
            value_parts.append("\n")
            for cmd, desc in self.commands:
                value_parts.append(f"\n**`{cmd}`** - {desc}")
        
        return {
            "name": self.name,
            "value": "".join(value_parts),
            "inline": False
        }
    
    def to_detailed_embed(self) -> discord.Embed:
        """Create a detailed embed for this module with all commands."""
        embed = discord.Embed(
            title=self.name,
            description=self.description,
            color=0x5865F2
        )
        
        if self.commands:
            lines = [f"**`{cmd}`** - {desc}" for cmd, desc in self.commands]
            chunks: list[str] = []
            current: list[str] = []
            current_len = 0
            for line in lines:
                line_len = len(line) + (1 if current else 0)
                if current and current_len + line_len > 1024:
                    chunks.append("\n".join(current))
                    current = [line]
                    current_len = len(line)
                else:
                    current.append(line)
                    current_len += line_len
            if current:
                chunks.append("\n".join(current))

            for index, chunk in enumerate(chunks, start=1):
                field_name = "Commands" if index == 1 else f"Commands (cont. {index})"
                embed.add_field(
                    name=field_name,
                    value=chunk,
                    inline=False
                )
        
        return embed


class HelpSystem:
    """
    Central help system that modules can register with.
    
    Usage:
        # In module initialization
        help_system.register_module(
            name="Scanner",
            description="Image hash scanning for suspicious content.",
            commands=[
                ("scanner enable", "Enable image scanning"),
                ("scanner status", "Check scanner status"),
            ]
        )
    """
    
    def __init__(self):
        self._modules: dict[str, ModuleHelp] = {}
        self._registered_order: list[str] = []
    
    def register_module(
        self,
        name: str,
        description: str,
        help_command: str = "",
        commands: Optional[list[tuple[str, str]]] = None,
        *,
        group: str = "",
        hidden: bool = False,
    ) -> None:
        """
        Register a module's help information.
        
        Args:
            name: Module name to display
            description: Brief description of the module
            help_command: Command to get detailed help (e.g., "scanner help")
            commands: List of (command, description) tuples
        """
        if name not in self._modules:
            self._registered_order.append(name)
        
        self._modules[name] = ModuleHelp(
            name=name,
            description=description,
            help_command=help_command,
            commands=commands or [],
            group=group,
            hidden=hidden,
        )
    
    def unregister_module(self, name: str) -> None:
        """Remove a module from the help system."""
        if name in self._modules:
            del self._modules[name]
            if name in self._registered_order:
                self._registered_order.remove(name)
    
    def get_help_embed(self, title: str = "Bot Modules") -> discord.Embed:
        """
        Generate a help embed with module overview.
        
        Args:
            title: Title for the help embed
            
        Returns:
            Discord embed with module overview and help commands
        """
        embed = discord.Embed(
            title=title,
            description="Here are all available modules. Use each module's help command for detailed information.",
            color=0x5865F2  # Discord blurple
        )
        
        for module_name in self._registered_order:
            if module_name in self._modules:
                module_help = self._modules[module_name]
                if module_help.hidden:
                    continue
                field = module_help.to_embed_field()
                embed.add_field(**field)
        
        embed.set_footer(text="Use @bot help to see this message again")
        return embed
    
    def has_modules(self) -> bool:
        """Check if any modules are registered."""
        return len(self._modules) > 0
    
    def get_module_embed(self, name: str) -> Optional[discord.Embed]:
        """
        Get detailed help embed for a specific module.
        
        Args:
            name: Module name (must match registered name exactly)
            
        Returns:
            Discord embed with module details, or None if not found
        """
        module_help = self._modules.get(name)
        if module_help is None:
            return None
        return module_help.to_detailed_embed()

    def get_module_help(self, name: str) -> Optional[discord.Embed]:
        """Backward-compatible wrapper for module help."""
        return self.get_module_embed(name)
    
    def get_module_names(self) -> list[str]:
        """Get list of registered module names in order."""
        return list(self._registered_order)

    def get_registered_modules(self, *, include_hidden: bool = True) -> list[ModuleHelp]:
        """Get registered ModuleHelp objects in display order."""
        out: list[ModuleHelp] = []
        for module_name in self._registered_order:
            module_help = self._modules.get(module_name)
            if module_help is None:
                continue
            if not include_hidden and module_help.hidden:
                continue
            out.append(module_help)
        return out

    def get_available_commands_embed(
        self,
        *,
        title: str,
        allow_mod: bool,
        allow_admin: bool,
        allow_owner: bool,
        include_hidden: bool = True,
        max_lines: int = 160,
    ) -> discord.Embed:
        """
        Generate an embed that only shows commands a user likely has access to.

        Filtering rule is based on `(mod only)`, `(admin only)`, `(owner only)` markers
        in the registered help descriptions.
        """
        def _allowed(desc: str) -> bool:
            d = (desc or "").lower()
            if "owner only" in d:
                return allow_owner
            if "admin only" in d:
                return allow_admin
            if "mod only" in d:
                return allow_mod
            return True

        lines: list[str] = []
        for module_help in self.get_registered_modules(include_hidden=include_hidden):
            for cmd, desc in module_help.commands:
                if not _allowed(desc):
                    continue
                lines.append(f"**`{cmd}`** - {desc}")

        if not lines:
            embed = discord.Embed(
                title=title,
                description="No commands available (based on current help markers).",
                color=0x5865F2,
            )
            return embed

        extra = 0
        if len(lines) > max_lines:
            extra = len(lines) - max_lines
            lines = lines[:max_lines]

        embed = discord.Embed(
            title=title,
            description="Commands shown are filtered by your Discord permissions where help text marks commands as mod/admin/owner-only.",
            color=0x5865F2,
        )

        chunks: list[str] = []
        current: list[str] = []
        current_len = 0
        for line in lines:
            line_len = len(line) + (1 if current else 0)
            if current and current_len + line_len > 1024:
                chunks.append("\n".join(current))
                current = [line]
                current_len = len(line)
            else:
                current.append(line)
                current_len += line_len
        if current:
            chunks.append("\n".join(current))

        for idx, chunk in enumerate(chunks, start=1):
            name = "Commands" if idx == 1 else f"Commands (cont. {idx})"
            embed.add_field(name=name, value=chunk, inline=False)

        if extra:
            embed.set_footer(text=f"…and {extra} more commands not shown. Use @bot help for the full module list.")
        else:
            embed.set_footer(text="Use @bot help for the full module list.")
        return embed


# Global singleton instance
help_system = HelpSystem()
