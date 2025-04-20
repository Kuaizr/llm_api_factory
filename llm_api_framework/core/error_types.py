from enum import Enum, auto

class ErrorType(Enum):
    """API错误类型标准定义"""
    NETWORK = auto()      # 网络问题
    RATE_LIMIT = auto()   # 速率限制
    QUOTA_EXCEEDED = auto() # 额度不足
    INVALID_REQUEST = auto() # 无效请求
    AUTH_FAILURE = auto()   # 认证失败
    SERVER_ERROR = auto()   # 服务端错误
    OTHER = auto()         # 其他错误

class Action(Enum):
    """错误处理动作"""
    RETRY = auto()     # 重试当前客户端
    SWITCH = auto()    # 切换客户端
    ABORT = auto()     # 中止并报错