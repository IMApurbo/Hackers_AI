* conf file

```
/root/.hackers_ai_mcp.json
```

```
{
  "mcpServers": {
    "ghidra": {
      "command": "/home/kali/myenv/bin/python3",
      "args": [
        "/home/kali/Downloads/GhidraMCP-release-1-4/bridge_mcp_ghidra.py",
                "--ghidra-server",
        "http://127.0.0.1:8080/"
      ]
    },
    
 "mcp-kali-server": {
      "command": "mcp-server",
        "args": [
          "--server",
          "http://127.0.0.1:5000/"
        ],
        "description": "MCP Kali Server",
        "timeout": 300,
        "alwaysAllow": []
    }
  }
}

```

* Tested on
  ```
  https://github.com/LaurieWired/GhidraMCP
  https://github.com/Wh0am123/MCP-Kali-Server

  ``` 
