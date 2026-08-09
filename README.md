<div align="center">

<!-- ARC ICON / LOGO SLOT -->

<!-- Replace this block with your HTML/SVG icon -->

# ARC V2

### A persistent, agent-based assistant architecture.

**Local inference · service-oriented · event-driven · built for proactivity**

<br>

![Status](https://img.shields.io/badge/status-active%20development-orange)
![Core](https://img.shields.io/badge/core-boot%20%2B%20service%20foundation-red)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![Architecture](https://img.shields.io/badge/architecture-service--oriented-purple)

</div>

---

## Overview

ARC V2 is **not** a generic chat agent. It is being built as a **persistent assistant system** with a central kernel, a separate runtime inference backend, and an event-driven service architecture.

The goal is to move beyond simple `prompt → response` behavior and toward an assistant that can **remember, plan, act, react to events, and eventually operate proactively**.

## Current status

> [!WARNING]
> ARC V2 is in early development.
>
> The **core boot path and service foundation** are in place, but the higher-level assistant logic is still being built.

## Core architecture

```text
Boot → Pulse → Services → Runtime → Kernel
```

* **Boot** starts the system
* **Pulse** supervises services
* **Runtime** handles model inference
* **Kernel** will coordinate context, planning, memory, and tasks
* **Services** provide system capabilities

## Concept map

| Concept | Doc                                    | Purpose                           |
| ------- | -------------------------------------- | --------------------------------- |
| Services    | [`docs/SERVICES.md`](./docs/SERVICES.md)       | How to add your own Service       |