"""
WOM Visualization Module
========================

利益ランドスケープの多次元可視化エンジン

Submodules:
- merit_order: Merit Order曲線生成
- regime_map: Regime map分類
- pareto_front: Pareto最適性分析
- hierarchical_triangulation: 階層的三角測量
"""

__version__ = "1.0.0"
__author__ = "Ohsugi (WOM Development Team)"

from .merit_order import MeritOrderAnalyzer

__all__ = [
    "MeritOrderAnalyzer",
]
