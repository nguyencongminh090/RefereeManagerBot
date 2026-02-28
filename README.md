# 🏆 Referee Manager Bot

A powerful, distributed automation bot designed to monitor, manage, and referee Gomoku (and other board games) tournaments on the **PlayOk** platform.

## 📖 Overview

Organizing large-scale online tournaments across multiple game rooms can be a logistical nightmare. **Referee Manager Bot** solves this by deploying automated "Bot Referees" (Clients) directly into individual game tables on PlayOk. 

These bots silently observe the matches in real-time and send the official results back to a Server, which automatically tallies scores, updates leaderboards, and safeguards tournament data against crashes.

## ✨ Features

- **👀 Automated Match Observation:** Uses Selenium to quietly monitor game chats and determine Match Start, Win, Loss, and Draw events without human intervention.
- **☁️ Centralized Cloud Scoring:** Multiple client bots feed match results simultaneously into a single Server, maintaining one centralized, accurate live leaderboard.
- **🛠️ Chat Command System:** Authorized human referees can control the bots directly via PlayOk's chat box (e.g., typing `!score` to see the leaderboard or `!start` to manually kick off a match).
- **⚡ Auto-Recovery & Persistence:** The central server automatically backs up the tournament scores to a JSON file every 5 minutes, surviving sudden power cuts or server crashes.
- **🔌 Immortal Multi-threading:** Hand-built TCP Sockets allow the server to handle dozens of bot referees concurrently without lagging or dropping packets.

## 🚀 Usage

*(Note: Provide instructions on how to install requirements and run the bot here once fully implemented).*

**1. Start the Central Server:**
```bash
python3 server.py
# The server will listen for incoming Bot connections and manage the Global Leaderboard.
```

**2. Deploy a Bot Referee (Client):**
```bash
python3 client.py
# The bot will open a browser, log into PlayOk, accept invitations to tables, and start sending scores.
```

## 📂 Project Structure

```text
RefereeManagerBot/
├── 📄 server.py            # Server Orchestrator / Entry point
├── 📄 client.py            # Client Orchestrator / Entry point
├── 📁 core/                # Core business & app logic
│   ├── 📄 driver.py        # Wrapper for Selenium WebDriver
│   ├── 📄 player.py        # Math & Data for Player, Team, and Manager
│   ├── 📄 session.py       # Match lifecycle and chat event handling
│   └── 📄 types.py         # Core Enums (SessionState, GameResult, PacketType)
├── 📁 network/             # Network communication layer
│   ├── 📄 client_socket.py # Multi-threaded TCP Client
│   ├── 📄 server_socket.py # Concurrent TCP Server 
│   └── 📄 protocol.py      # TCP Framing and payload encoding/decoding
└── 📁 commands/            # Chatbot command routing
    ├── 📄 dispatcher.py    # Command routing and Context encapsulation
    └── 📄 handlers.py      # Execution logic for specific referee commands
```
