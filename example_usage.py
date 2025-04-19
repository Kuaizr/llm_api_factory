from llm_api_framework.core.manager import APIManager
import asyncio

async def test_async_stream_conversation():
    # 初始化manager
    manager = APIManager("configs/qwen2.5-7B-Instruct.json")
    
    # 第一轮流式对话
    print("第一轮流式对话: 自我介绍")
    print("AI回复(流式): ", end="", flush=True)
    full_response = ""
    async for chunk in manager.stream_api_async("你好，请介绍一下你自己"):
        if "content" in chunk:
            print(chunk["content"], end="", flush=True)
            full_response += chunk["content"]
        elif "error" in chunk:
            print("\n错误:", chunk["error"])
    print("\n")
    
    # 第二轮流式对话
    print("第二轮流式对话: 询问Python类")
    print("AI回复(流式): ", end="", flush=True)
    full_response = ""
    async for chunk in manager.stream_api_async("请用Python代码演示如何创建一个类"):
        if "content" in chunk:
            print(chunk["content"], end="", flush=True)
            full_response += chunk["content"]
        elif "error" in chunk:
            print("\n错误:", chunk["error"])
    print("\n")
    
    # 打印完整对话历史
    print("\n完整对话历史:")
    for i, msg in enumerate(manager.get_conversation_history(), 1):
        print(f"{i}. {msg['role']}: {msg['content']}")

if __name__ == "__main__":
    asyncio.run(test_async_stream_conversation())
