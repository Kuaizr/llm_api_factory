from setuptools import setup, find_packages

setup(
    name="llm_api_framework",
    version="0.1.0",
    author="kuaizhirui",
    author_email="kuaizhirui@gmail.com",
    description="一个轻量级、可扩展的多平台LLM API集成框架，提供统一的接口调用不同大模型平台API",
    packages=find_packages(),
    install_requires=[
        "openai>=1.0.0",
        "requests>=2.28.0",
        "aiohttp>=3.8.0",
        "python-dotenv>=0.19.0",
        "tqdm>=4.0.0",
        "Pillow>=11.2.1",
        "watchdog>=2.0.0"
    ],
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires='>=3.6',
)