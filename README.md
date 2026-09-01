# CloudStack–BlueField Integration API

REST integration service between Apache CloudStack and NVIDIA BlueField
eSwitch Management.

## Current status

Project scaffold only. Runtime logic has not yet been implemented.

## Development environment

- Python 3.12+
- FastAPI
- Mock eSwitch adapter on the CloudStack host
- Unix socket adapter when deployed on BlueField

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
