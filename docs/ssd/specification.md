# NeuroCore Specification Details

## Scope

This specification defines the public v1 behavior for the community repository.

## Stable Contracts

- capture stores records and documents with metadata and isolation-aware fields
- query returns deterministic results with optional semantic ranking
- CLI, HTTP, and MCP surfaces delegate to the same interface contracts
- admin and maintenance actions remain explicitly gated by configuration

## Non-Goals

- embedding real credentials in examples
- shipping private operational data
- relying on unpublished internal services
