"""
配置模块 - 统一管理API密钥和URL
从环境变量中读取各种API的配置信息
"""
import os
from pathlib import Path
from dotenv import load_dotenv, find_dotenv

# 自动向上查找项目根目录的.env文件（支持在任何子目录中调用）
dotenv_path = find_dotenv(usecwd=True)
if dotenv_path:
    load_dotenv(dotenv_path)
else:
    # 如果找不到，尝试从当前文件所在目录查找
    current_dir = Path(__file__).resolve().parent
    env_path = current_dir / ".env"
    load_dotenv(env_path)


class Config:
    """统一配置类"""
    
    # OpenAI 配置
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    
    # Anthropic (Claude) 配置
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
    
    # NVIDIA 配置
    NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")
    NVIDIA_BASE_URL = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
    
    # Coze 配置
    COZE_API_KEY = os.getenv("COZE_API_KEY", "")
    
    # Dify 配置
    DIFY_API_KEY = os.getenv("DIFY_API_KEY", "")
    
    # LangChain 配置
    LANGCHAIN_API_KEY = os.getenv("LANGCHAIN_API_KEY", "")
    
    @classmethod
    def get_openai_config(cls) -> dict:
        """获取OpenAI配置"""
        return {
            "api_key": cls.OPENAI_API_KEY,
            "base_url": cls.OPENAI_BASE_URL
        }
    
    @classmethod
    def get_nvidia_config(cls) -> dict:
        """获取NVIDIA配置"""
        return {
            "api_key": cls.NVIDIA_API_KEY,
            "base_url": cls.NVIDIA_BASE_URL
        }
    
    @classmethod
    def get_anthropic_config(cls) -> dict:
        """获取Anthropic配置"""
        return {
            "api_key": cls.ANTHROPIC_API_KEY
        }


# 为了方便使用，直接导出配置实例
config = Config()
