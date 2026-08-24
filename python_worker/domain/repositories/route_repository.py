"""
仓储接口 —— 路线仓储

定义路线数据的获取抽象。基础设施层可实现为高德API、本地缓存或Mock。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from ..model.route import Route
from ..model.navigation_request import NavigationRequest


class IRouteRepository(ABC):
    """路线仓储接口。"""

    @abstractmethod
    def fetch_routes(self, request: NavigationRequest) -> List[Route]:
        """
        根据导航请求获取多条备选步行路线。

        Args:
          request: 导航请求（含起终点）

        Returns:
          备选路线列表（至少1条，通常3~5条）
        """
        ...
