# semabridge migrate (include SML; can specify “OSI/SML” in yaml)

Status: In Progress
Priority: High
Time: Time-intensive
assigned on : January 28, 2026

SML to OSI 

OSI to SML

duckdb - change old to new schema; don’t delete old scheme

---

---

# Semabridge: Interoperability & Storage Requirements

**Scope:** OSI/SML Interop, Repository Migration, and Installation Configuration.

reference for OSI: [https://github.com/open-semantic-interchange/OSI](https://github.com/open-semantic-interchange/OSI)

## 1. Interoperability Requirements (OSI <-> SML)

Semabridge must now support **two** potential canonical forms: **SML** (Semantic Modeling Language) and **OSI**(Open Semantic Interface). The system must be able to translate between them losslessly where possible.

- **REQ-INT-001 (Bidirectional Conversion):**
    - The system must implement a dedicated internal bridge to convert **SML $\leftrightarrow$ OSI**.
    - This conversion must be invokable at any stage of the pipeline (e.g., convert an archived SML artifact to OSI for a new target connector that only speaks OSI).
- **REQ-INT-002 (On-the-Fly Bridging):**
    - If a Source Connector emits **SML** but the Target Connector requires **OSI**, the system must automatically inject the `SmlToOsiConverter` into the pipeline without user intervention.
- **REQ-INT-003 (Artifact duality):**
    - When `persist_artifacts: true` is enabled, the system should optionally store *both* the SML and OSI representations of the model for maximum compatibility, regardless of which was used for the conversion.

## 2. Repository Management Requirements (DuckDB)

To ensure data safety during updates (e.g., Semabridge v1.0 $\rightarrow$ v1.1), the embedded DuckDB repository must support transactional migrations with rollback capabilities.

- **REQ-REP-001 (Non-Destructive Migration):**
    - When the CLI detects a schema version mismatch (newer code, older DB), it must **copy** the existing DuckDB file (e.g., `semabridge.db` $\rightarrow$ `semabridge.v1.bak`) *before* applying any `ALTER TABLE` or migration scripts.
    - The original data must never be mutated in place until the backup is confirmed successful.
- **REQ-REP-002 (Atomic Porting):**
    - The migration process must create a **new** database file with the new schema and "port" (insert) the data from the old file.
    - This ensures that if the migration crashes halfway, the old database remains untouched and valid.
- **REQ-REP-003 (Rollback Capability):**
    - The CLI must support a command (e.g., `semabridge system rollback`) that restores the repository to the previous version's backup file if a migration introduces critical bugs.

## 3. Installation & Configuration Requirements

We need to handle the user's preference for the "Default Intermediary" (the common language used for modeling).

- **REQ-INS-001 (First-Run Configuration - `semabridge init`):**
    - The `init` command must prompt the user to select their preferred standard:
        
        > "Select default intermediate model: [1] OSI (Default), [2] SML"
        > 
    - This choice must be persisted in a global configuration file (e.g., `~/.semabridge/config.yaml`).
- **REQ-INS-002 (Default Fallback Policy):**
    - If the user runs the system without running `init` or defining a preference in `semabridge.yaml`, the system **MUST default to OSI**.
- **REQ-INS-003 (Project Override):**
    - A specific project's `semabridge.yaml` can override the global default.
    - *Example:* User's global default is OSI, but a legacy project explicitly sets `intermediary: sml`.

---

## 4. Implementation Reference

### 4.1 Global Configuration (`~/.semabridge/config.yaml`)

YAML

`core:
  # REQ-INS-002: Default to "osi" if this file/key is missing
  default_intermediary: "osi" 
  
  # REQ-REP-003: Location of automatic backups
  backup_retention: 5  # Keep last 5 schema versions`

### CLI Experience (sample)

Bash

`$ semabridge init

Welcome to Semabridge! Let's get you started
----------------------
Which standard would you like to use as your default intermediate model?
1) OSI (Open Semantic Interface) [Default]
2) SML (Semantic Modeling Language)

Choice [1]: <ENTER>

Configuration saved to ~/.semabridge/config.yaml.
Default Intermediary: OSI`
