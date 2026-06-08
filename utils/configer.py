import json
import os
from typing import Any, Optional


class ConfigManager:
    """配置管理类"""

    def __init__(self, config_path: str = None):
        """
        初始化配置管理器

        Args:
            config_path: 配置文件路径，默认为当前目录下的 configs/config.json
        """
        if config_path is None:
            # 获取项目根目录（utils 的上一级）
            current_dir = os.path.dirname(
                os.path.dirname(os.path.abspath(__file__)))
            self.config_path = os.path.join(
                current_dir, 'configs', 'config.json')
        else:
            self.config_path = config_path

        self._ensure_config_dir()
        self._ensure_config_file()

    def _ensure_config_dir(self):
        """确保配置目录存在"""
        config_dir = os.path.dirname(self.config_path)
        if config_dir and not os.path.exists(config_dir):
            os.makedirs(config_dir)

    def _ensure_config_file(self):
        """确保配置文件存在，如果不存在则创建空配置"""
        if not os.path.exists(self.config_path):
            self._save_config({})

    def _load_config(self) -> dict:
        """加载配置文件"""
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return {}

    def _save_config(self, config: dict):
        """保存配置文件"""
        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)

    def get_global_config(self) -> dict:
        """
        获取全局配置（直接返回json内容）

        Returns:
            配置字典
        """
        return self._load_config()

    def get_field(self, key: str, default: Any = None) -> Any:
        """
        获取指定字段

        Args:
            key: 字段名，支持点号分隔的多级key，如 "database.host"
            default: 如果字段不存在返回的默认值，默认为 None

        Returns:
            字段值，如果不存在返回 default
        """
        config = self._load_config()
        keys = key.split('.')

        current = config
        for k in keys:
            if isinstance(current, dict) and k in current:
                current = current[k]
            else:
                return default
        return current

    def set_field(self, key: str, value: Any) -> bool:
        """
        修改指定字段（不存在则添加）

        Args:
            key: 字段名，支持点号分隔的多级key，如 "database.host"
            value: 要设置的值

        Returns:
            是否修改成功
        """
        try:
            config = self._load_config()
            keys = key.split('.')

            # 导航到要设置的父级
            current = config
            for k in keys[:-1]:
                if k not in current:
                    current[k] = {}
                elif not isinstance(current[k], dict):
                    # 如果中间节点不是字典，则覆盖为字典
                    current[k] = {}
                current = current[k]

            # 设置最终的值
            current[keys[-1]] = value

            # 保存配置
            self._save_config(config)
            return True
        except Exception as e:
            print(f"设置字段失败: {e}")
            return False

    def update_global_config(self, new_config: dict, merge: bool = True) -> bool:
        """
        修改全局配置文件

        Args:
            new_config: 新的配置字典
            merge: 是否合并现有配置（True: 合并，False: 完全替换）

        Returns:
            是否修改成功
        """
        try:
            if merge:
                config = self._load_config()
                self._deep_merge(config, new_config)
            else:
                config = new_config

            self._save_config(config)
            return True
        except Exception as e:
            print(f"更新配置失败: {e}")
            return False

    def _deep_merge(self, base: dict, update: dict):
        """深度合并两个字典"""
        for key, value in update.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = value

    def delete_field(self, key: str) -> bool:
        """
        删除指定字段

        Args:
            key: 字段名，支持点号分隔的多级key

        Returns:
            是否删除成功
        """
        try:
            config = self._load_config()
            keys = key.split('.')

            current = config
            for k in keys[:-1]:
                if not isinstance(current, dict) or k not in current:
                    return False
                current = current[k]

            if keys[-1] in current:
                del current[keys[-1]]
                self._save_config(config)
                return True
            return False
        except Exception as e:
            print(f"删除字段失败: {e}")
            return False

    def reload(self) -> dict:
        """
        重新加载配置

        Returns:
            重新加载后的配置
        """
        return self._load_config()


_config_manager = None


def get_config_manager() -> ConfigManager:
    """获取配置管理器单例"""
    global _config_manager
    if _config_manager is None:
        _config_manager = ConfigManager()
    return _config_manager


# 便捷函数（使用单例）
def get_global_config() -> dict:
    """获取全局配置"""
    return get_config_manager().get_global_config()


def get_field(key: str, default: Any = None) -> Any:
    """获取指定字段"""
    return get_config_manager().get_field(key, default)


def set_field(key: str, value: Any) -> bool:
    """修改指定字段"""
    return get_config_manager().set_field(key, value)


def update_global_config(new_config: dict, merge: bool = True) -> bool:
    """修改全局配置文件"""
    return get_config_manager().update_global_config(new_config, merge)


def delete_field(key: str) -> bool:
    """删除指定字段"""
    return get_config_manager().delete_field(key)
