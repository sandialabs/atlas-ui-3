# Atlas UI 3

[![CI/CD Pipeline](https://github.com/sandialabs/atlas-ui-3/actions/workflows/ci.yml/badge.svg)](https://github.com/sandialabs/atlas-ui-3/actions/workflows/ci.yml)
[![Security Checks](https://github.com/sandialabs/atlas-ui-3/actions/workflows/security.yml/badge.svg)](https://github.com/sandialabs/atlas-ui-3/actions/workflows/security.yml)
[![Docker Image](https://ghcr-badge.egpl.dev/sandialabs/atlas-ui-3/latest_tag?trim=major&label=latest)](https://github.com/sandialabs/atlas-ui-3/pkgs/container/atlas-ui-3)
![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)
![React 19](https://img.shields.io/badge/react-19.2-blue.svg)
![License MIT](https://img.shields.io/badge/license-MIT-blue.svg)

A modern LLM chat interface with MCP (Model Context Protocol) integration.

![Screenshot](docs/readme_img/screenshot-11-6-2025image.png)

## About the Project

**Atlas UI 3** is a full-stack LLM chat interface that supports multiple AI models, including those from OpenAI, Anthropic, and Google. Its core feature is the integration with the Model Context Protocol (MCP), which allows the AI assistant to connect to external tools and data sources, enabling complex, real-time workflows.

### Features

*   **Multi-LLM Support**: Connect to various LLM providers.
*   **MCP Integration**: Extend the AI's capabilities with custom tools.
*   **RAG Support**: Enhance responses with Retrieval-Augmented Generation.
*   **3D Visualization**: Render engineering and scientific files with VTK.js (STL, VTK, OBJ, PLY, and more).
*   **Secure and Configurable**: Features group-based access control, compliance levels, and a tool approval system.
*   **Modern Stack**: Built with React 19, FastAPI, and WebSockets.

## Documentation

We have created a set of comprehensive guides to help you get the most out of Atlas UI 3.

*   **[Getting Started](./docs/01_getting_started.md)**: The perfect starting point for all users. This guide covers how to get the application running with Docker or on your local machine.

*   **[Administrator's Guide](./docs/02_admin_guide.md)**: For those who will deploy and manage the application. This guide details configuration, security settings, access control, and other operational topics.

*   **[Developer's Guide](./docs/03_developer_guide.md)**: For developers who want to contribute to the project. It provides an overview of the architecture and instructions for creating new MCP servers.

*   **[VTK Visualization Guide](./docs/VTK_VISUALIZATION.md)**: Learn about the 3D visualization capabilities using VTK.js. This guide covers supported file formats (VTK, STL, OBJ, PLY, etc.) and interactive features.

## For AI Agent Contributors

If you are an AI agent working on this repository, please refer to the following documents for the most current and concise guidance:

*   **[CLAUDE.md](./CLAUDE.md)**: Detailed architecture, workflows, and conventions.
*   **[GEMINI.md](./GEMINI.md)**: Gemini-specific instructions.
*   **[.github/copilot-instructions.md](./.github/copilot-instructions.md)**: A compact guide for getting productive quickly.

## License

Copyright 2025 National Technology & Engineering Solutions of Sandia, LLC (NTESS). Under the terms of Contract DE-NA0003525 with NTESS, the U.S. Government retains certain rights in this software

MIT License

