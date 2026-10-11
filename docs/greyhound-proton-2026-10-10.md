# Greyhound Racing dependency diagnostic (October 10, 2026)

**Repository:** GTA L-Hook (`dereksparks1982/gtalhook`), not GTA520. **Branch:** `main`.

## Decisions to preserve

- **Steam Proton 10.0 stable** for GTA V Legacy app 271590. No Proton Experimental or nightly runtime.
- **Stable ScriptHookVDotNet archive only**, pinned by SHA-256 `b6b4b61bce0437e295ebab065ca437c95ed938e0169197726150047861478211`. No nightly ScriptHookVDotNet.
- Preserve the existing working `ScriptHookV.dll` and GTA V data. No GTA downgrade or modifications to GTA520.
- A command with `--install` is authorization to proceed without a second typed confirmation.
- Keep an independent, reversible installer; no third-party binaries committed.

## Confirmed failure before this patch

The October 10 v1.1 run found the user's GTA V Legacy installation and Proton prefix and made a full **1.45 GiB backup**. It found a .NET Framework 4.8 registry entry, then failed running `protontricks 271590 vcrun2019` (exit code 1). The log identified `Proton Experimental` and included unknown Wine architecture, WoW64 detection and empty `%AppData%` errors. The installer stopped before copying the Greyhound mod. No evidence yet that Visual C++ installed.

## v1.3 source and safeguards

- Standalone script: [Greyhound-Proton-Test-v1.3.py](../tools/greyhound/Greyhound-Proton-Test-v1.3.py).
- Detects an installed `Proton 10.0` tool; refuses to substitute Experimental or an unknown tool.
- Checks GTA's entry in Steam `config.vdf` `CompatToolMapping`, sets **app 271590 only** to `proton_10` when required, and backs up the prior Steam configuration first. Never rewrites the mapping while the Steam process is running. Refuses missing or ambiguous configuration rather than guessing.
- Creates the usual full Proton-prefix snapshot and game-file manifest before attempting dependencies.
- Rejects mismatched ScriptHookVDotNet archives using the above SHA-256.
- Leaves existing base Script Hook V in place and retains the explicit rollback and log collection commands.
- Contains no INSTALL/RESTORE confirmation prompts.

**Important limitation:** The Steam config mapping and the use of Protontricks are not themselves proof that this resolves the prior Visual C++ failure. The patch has **not been run on the user's PC**. The pinned stable ScriptHookVDotNet 3.6.0 also has a known issue with GTA V build 3258 and later. Treat results as a compatibility diagnostic, not a validated GTA L-Hook runtime or a release candidate.

## Offline verification

Five offline unit tests exercised the Steam `CompatToolMapping` editing logic: replacing GTA's old mapping without touching other apps, idempotency, adding a missing override, and safe rejection of malformed or unsupported configurations. All five passed locally. See [tests](../tools/greyhound/tests/test_proton_pin.py).

No game execution, user machine change, release tag, or accepted in-game outcome has been claimed.
