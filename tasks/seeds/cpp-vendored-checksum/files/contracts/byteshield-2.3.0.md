# ByteShield 2.3.0 vendoring contract

The approved offline refresh is the already-staged `vendor/byteshield` tree.
Its release identity is `2.3.0`. No configure, build, or test step may contact a
network or select a system ByteShield.

The canonical source manifest contains these paths in this exact order:

1. `CMakeLists.txt`
2. `include/byteshield/byteshield.h`
3. `src/byteshield.c`

For each path, append `<path>:<lowercase SHA-256 of raw file bytes>\n`, then
SHA-256 the UTF-8 manifest bytes. The approved manifest digest is
`9e1069c41bbe4820f08b27177a29dc8a85869bbd81e48799b9679a8cef35e1ec`.
The raw `LICENSE` digest is
`dcca3abbeb35b79172c5a55148443216b63c77d45ba787009b350327e0e94fee`
and remains the MIT license shipped with 2.3.0.

The downstream patch `vendor/patches/0001-prefix-public-symbol.patch` must stay
locked at
`289802db75518c9c0d62cbc2cfe16836f4f8d4877f02838a1addfeae6f867a23`.
Its result is already represented in the staged tree: `byteshield_mix` is prefixed to
`streamseal_vendor_byteshield_mix`. This private name must not enter the shared
library's dynamic ABI.

ByteShield is an embedded object target. Its tests, tools, standalone install,
and native-unaligned implementation are disabled by the parent before adding
the subdirectory. The objects are position independent with hidden C symbol
visibility so they can be included in either static or shared StreamSeal.

The installed CMake target remains `StreamSeal::streamseal` and is relocatable.
The installed public header remains `streamseal/streamseal.h`; ABI major 3
exports exactly `streamseal_checksum` and `streamseal_abi_version`. For bytes
`53 65 61 6c 21 00 ff` and seed `0x13579bdf`, the checksum is `316f6513`.
