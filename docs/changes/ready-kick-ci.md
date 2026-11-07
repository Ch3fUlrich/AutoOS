# Ready kick CI repair (D-4629)

Shard f stopped in the fake USB unmount-failure fixture after the FAT32 size
refusal test. Its fake mount commands still allowed a real global `sync`,
which waits on unrelated host filesystems. Both copy fixtures now stub sync;
a regression check requires those stubs. HTTP fixture shutdown waits are
bounded to five seconds before forced termination. No tests were removed.

The main merge retains compaction-driven handoff and immediate ready recycling.
