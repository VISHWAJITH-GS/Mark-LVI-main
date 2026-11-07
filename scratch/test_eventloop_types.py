import asyncio
import sys

print("Python version:", sys.version)
try:
    print("Testing ProactorEventLoop in main thread...")
    p_loop = asyncio.ProactorEventLoop()
    print("ProactorEventLoop:", p_loop)
except BaseException as e:
    print("Proactor error:", type(e), e)

try:
    print("Testing SelectorEventLoop in main thread...")
    s_loop = asyncio.SelectorEventLoop()
    print("SelectorEventLoop:", s_loop)
except BaseException as e:
    print("Selector error:", type(e), e)

