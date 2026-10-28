"""Voice for the Juniper (ADR-023): listen (VAD, STT), speak (TTS) and the voice session.

A layer around the agent: audio becomes text before the chat turn and the reply becomes audio
after it. Nothing here touches `brain/core`.
"""
