"""
Analytics service for tracking and reporting bot statistics.
"""
import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from pathlib import Path


class AnalyticsService:
    """Service for tracking and analyzing bot usage and commission statistics."""

    def __init__(self, data_dir: str = "data/analytics"):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)

    def _get_guild_dir(self, guild_id: int) -> str:
        """Get the directory path for a specific guild's analytics."""
        path = os.path.join(self.data_dir, str(guild_id))
        os.makedirs(path, exist_ok=True)
        os.makedirs(os.path.join(path, "timeseries"), exist_ok=True)
        return path

    def _get_stats_file(self, guild_id: int) -> str:
        """Get the stats file path for a guild."""
        return os.path.join(self._get_guild_dir(guild_id), "stats.json")

    def _get_timeseries_file(self, guild_id: int, metric: str, period: str) -> str:
        """Get the timeseries file path for a specific metric and period."""
        return os.path.join(self._get_guild_dir(guild_id), "timeseries", f"{metric}_{period}.json")

    def _load_stats(self, guild_id: int) -> Dict[str, Any]:
        """Load stats for a guild."""
        stats_file = self._get_stats_file(guild_id)
        if os.path.exists(stats_file):
            with open(stats_file, 'r') as f:
                return json.load(f)
        return {
            "commission_stats": {
                "total_completed": 0,
                "total_value": 0.0,
                "avg_completion_time_hours": 0,
                "by_month": {},
                "by_type": {}
            },
            "profile_stats": {},
            "bot_stats": {
                "uptime_start": datetime.now().isoformat(),
                "commands_run": 0,
                "messages_scanned": 0
            },
            "last_updated": datetime.now().isoformat()
        }

    def _save_stats(self, guild_id: int, stats: Dict[str, Any]):
        """Save stats for a guild."""
        stats["last_updated"] = datetime.now().isoformat()
        stats_file = self._get_stats_file(guild_id)
        with open(stats_file, 'w') as f:
            json.dump(stats, f, indent=2)

    def _load_timeseries(self, guild_id: int, metric: str, period: str) -> List[Dict[str, Any]]:
        """Load timeseries data for a metric."""
        ts_file = self._get_timeseries_file(guild_id, metric, period)
        if os.path.exists(ts_file):
            with open(ts_file, 'r') as f:
                return json.load(f)
        return []

    def _save_timeseries(self, guild_id: int, metric: str, period: str, data: List[Dict[str, Any]]):
        """Save timeseries data for a metric."""
        ts_file = self._get_timeseries_file(guild_id, metric, period)
        with open(ts_file, 'w') as f:
            json.dump(data, f, indent=2)

    def record_event(self, guild_id: int, event_type: str, data: Dict[str, Any]):
        """
        Record an analytics event.

        Args:
            guild_id: Guild ID
            event_type: Type of event (e.g., "commission_completed", "command_run")
            data: Event data
        """
        stats = self._load_stats(guild_id)
        now = datetime.now()

        if event_type == "commission_completed":
            # Update commission stats
            commission_stats = stats["commission_stats"]
            commission_stats["total_completed"] += 1
            commission_stats["total_value"] += data.get("price", 0)

            # Calculate completion time
            if "completion_time_hours" in data:
                total = commission_stats["total_completed"]
                avg = commission_stats["avg_completion_time_hours"]
                new_avg = ((avg * (total - 1)) + data["completion_time_hours"]) / total
                commission_stats["avg_completion_time_hours"] = new_avg

            # By month
            month_key = now.strftime("%Y-%m")
            if month_key not in commission_stats["by_month"]:
                commission_stats["by_month"][month_key] = {"count": 0, "value": 0}
            commission_stats["by_month"][month_key]["count"] += 1
            commission_stats["by_month"][month_key]["value"] += data.get("price", 0)

            # By type
            comm_type = data.get("type", "unknown")
            if comm_type not in commission_stats["by_type"]:
                commission_stats["by_type"][comm_type] = {"count": 0, "value": 0}
            commission_stats["by_type"][comm_type]["count"] += 1
            commission_stats["by_type"][comm_type]["value"] += data.get("price", 0)

            # Record timeseries data
            ts_data = self._load_timeseries(guild_id, "commissions", "daily")
            ts_data.append({
                "timestamp": now.isoformat(),
                "type": comm_type,
                "value": data.get("price", 0),
                "completion_time_hours": data.get("completion_time_hours", 0)
            })
            # Keep only last 90 days
            cutoff = (now - timedelta(days=90)).isoformat()
            ts_data = [d for d in ts_data if d["timestamp"] > cutoff]
            self._save_timeseries(guild_id, "commissions", "daily", ts_data)

        elif event_type == "profile_view":
            # Update profile stats
            user_id = str(data.get("user_id"))
            if user_id not in stats["profile_stats"]:
                stats["profile_stats"][user_id] = {
                    "total_views": 0,
                    "views_by_week": {},
                    "portfolio_views": {}
                }
            stats["profile_stats"][user_id]["total_views"] += 1

            # By week
            week_key = now.strftime("%Y-W%U")
            if week_key not in stats["profile_stats"][user_id]["views_by_week"]:
                stats["profile_stats"][user_id]["views_by_week"][week_key] = 0
            stats["profile_stats"][user_id]["views_by_week"][week_key] += 1

        elif event_type == "portfolio_view":
            # Update portfolio entry stats
            user_id = str(data.get("user_id"))
            entry_id = data.get("entry_id")
            if user_id not in stats["profile_stats"]:
                stats["profile_stats"][user_id] = {
                    "total_views": 0,
                    "views_by_week": {},
                    "portfolio_views": {}
                }
            if entry_id not in stats["profile_stats"][user_id]["portfolio_views"]:
                stats["profile_stats"][user_id]["portfolio_views"][entry_id] = 0
            stats["profile_stats"][user_id]["portfolio_views"][entry_id] += 1

        elif event_type == "command_run":
            stats["bot_stats"]["commands_run"] += 1

        elif event_type == "message_scanned":
            stats["bot_stats"]["messages_scanned"] += 1

        self._save_stats(guild_id, stats)

    def get_commission_stats(self, guild_id: int, period: Optional[str] = None) -> Dict[str, Any]:
        """
        Get commission statistics for a guild.

        Args:
            guild_id: Guild ID
            period: Optional period filter ("month", "year", "all")

        Returns:
            Dictionary of commission statistics
        """
        stats = self._load_stats(guild_id)
        commission_stats = stats["commission_stats"]

        if period == "month":
            now = datetime.now()
            month_key = now.strftime("%Y-%m")
            return commission_stats["by_month"].get(month_key, {"count": 0, "value": 0})
        elif period == "year":
            now = datetime.now()
            year = now.year
            year_stats = {"count": 0, "value": 0}
            for month_key, month_stats in commission_stats["by_month"].items():
                if month_key.startswith(str(year)):
                    year_stats["count"] += month_stats["count"]
                    year_stats["value"] += month_stats["value"]
            return year_stats
        else:
            return commission_stats

    def get_profile_stats(self, user_id: int) -> Dict[str, Any]:
        """
        Get profile statistics for a user across all guilds.

        Args:
            user_id: User ID

        Returns:
            Dictionary of profile statistics
        """
        combined_stats = {
            "total_views": 0,
            "views_by_week": {},
            "portfolio_views": {}
        }

        # Aggregate stats from all guilds
        for guild_dir in os.listdir(self.data_dir):
            guild_path = os.path.join(self.data_dir, guild_dir)
            if not os.path.isdir(guild_path):
                continue

            try:
                guild_id = int(guild_dir)
                stats = self._load_stats(guild_id)
                user_stats = stats["profile_stats"].get(str(user_id), {})

                combined_stats["total_views"] += user_stats.get("total_views", 0)

                # Merge views by week
                for week, count in user_stats.get("views_by_week", {}).items():
                    combined_stats["views_by_week"][week] = combined_stats["views_by_week"].get(week, 0) + count

                # Merge portfolio views
                for entry_id, count in user_stats.get("portfolio_views", {}).items():
                    combined_stats["portfolio_views"][entry_id] = combined_stats["portfolio_views"].get(entry_id, 0) + count
            except (ValueError, KeyError):
                continue

        return combined_stats

    def get_bot_stats(self) -> Dict[str, Any]:
        """
        Get global bot statistics.

        Returns:
            Dictionary of bot statistics
        """
        combined_stats = {
            "total_commands_run": 0,
            "total_messages_scanned": 0,
            "guilds_tracked": 0,
            "uptime_seconds": 0
        }

        earliest_start = None

        for guild_dir in os.listdir(self.data_dir):
            guild_path = os.path.join(self.data_dir, guild_dir)
            if not os.path.isdir(guild_path):
                continue

            try:
                guild_id = int(guild_dir)
                stats = self._load_stats(guild_id)
                bot_stats = stats.get("bot_stats", {})

                combined_stats["total_commands_run"] += bot_stats.get("commands_run", 0)
                combined_stats["total_messages_scanned"] += bot_stats.get("messages_scanned", 0)
                combined_stats["guilds_tracked"] += 1

                # Track earliest uptime start
                uptime_start = bot_stats.get("uptime_start")
                if uptime_start:
                    start_dt = datetime.fromisoformat(uptime_start)
                    if earliest_start is None or start_dt < earliest_start:
                        earliest_start = start_dt
            except (ValueError, KeyError):
                continue

        # Calculate uptime
        if earliest_start:
            combined_stats["uptime_seconds"] = int((datetime.now() - earliest_start).total_seconds())

        return combined_stats

    def calculate_trends(self, guild_id: int, metric: str) -> List[Dict[str, Any]]:
        """
        Calculate trends for a metric.

        Args:
            guild_id: Guild ID
            metric: Metric name (e.g., "commissions", "profile_views")

        Returns:
            List of trend data points
        """
        if metric == "commissions":
            ts_data = self._load_timeseries(guild_id, "commissions", "daily")

            # Group by week for trending
            weekly_data = {}
            for entry in ts_data:
                timestamp = datetime.fromisoformat(entry["timestamp"])
                week_key = timestamp.strftime("%Y-W%U")
                if week_key not in weekly_data:
                    weekly_data[week_key] = {
                        "count": 0,
                        "total_value": 0,
                        "avg_completion_time": 0
                    }
                weekly_data[week_key]["count"] += 1
                weekly_data[week_key]["total_value"] += entry.get("value", 0)
                weekly_data[week_key]["avg_completion_time"] += entry.get("completion_time_hours", 0)

            # Calculate averages
            trends = []
            for week, data in sorted(weekly_data.items()):
                if data["count"] > 0:
                    data["avg_completion_time"] /= data["count"]
                trends.append({
                    "week": week,
                    **data
                })

            return trends

        return []
