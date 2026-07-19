# CMake 3.21 target and policy migration contract (protected local copy)

This project supports CMake 3.21 and newer. The migration boundary is local so
acceptance does not depend on documentation sites.

- CMP0077 is `NEW`: `option()` honors a normal variable supplied by a parent
  project instead of deleting it and replacing it with the option default.
- Compiler features, warnings, definitions, and include paths belong to the
  target that requires them. Directory-wide include commands and mutations of
  `CMAKE_CXX_FLAGS` are not part of the modern target contract.
- A public header path is expressed with separate `BUILD_INTERFACE` and
  `INSTALL_INTERFACE` usage requirements. Installed targets must not retain a
  source-tree path.
- The exported target remains `Telemetry::codec`. Linking that target supplies
  the public include directory to a downstream consumer.
- `BUILD_SHARED_LIBS=OFF` produces the static library and `ON` produces the
  shared library. A parent normal-variable override is honored under CMP0077.
- Package configuration and exported targets are relocatable after install.
