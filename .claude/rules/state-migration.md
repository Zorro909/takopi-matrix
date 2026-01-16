# State Migration Pattern

When persisting user/config state to disk, always support forward migration.

## Pattern

1. **Version your state files** - Include a `version` field in the root object
2. **Implement migration functions** - When loading state with older version, migrate to current
3. **Preserve user data** - Never drop existing configuration on version bump
4. **Atomic writes after migration** - Save migrated state back to disk

## Example

```python
def _migrate_state(self, data: dict, from_version: int) -> dict:
    if from_version == 1 and CURRENT_VERSION == 2:
        # Add new fields with defaults, preserve existing data
        for item in data.get("items", {}).values():
            item.setdefault("new_field", None)
        return {"version": 2, **data}
    return data
```

## DO
- Check version on load, migrate if needed
- Add new fields with sensible defaults
- Log migrations for debugging

## DON'T
- Drop state on unknown version (reset to empty)
- Require manual migration steps from users
- Silently lose user configuration

## Source Sessions
- 2026-01-14-takopi-matrix: Created JsonStateStore with hot-reload
- 2026-01-16: Added v1->v2 migration to preserve room preferences
