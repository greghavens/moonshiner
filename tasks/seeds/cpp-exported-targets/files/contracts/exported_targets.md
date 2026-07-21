# Installed target contract

BeaconQueue exports one public target named `Beacon::queue`. Its public usage
requirements include the installed header directory and `Threads::Threads`.
Those requirements must be identical for build-tree and installed consumers.

An installed package configuration owns discovery of every external target
named by its exports. It includes `CMakeFindDependencyMacro` and calls
`find_dependency(Threads)` before loading `BeaconQueueTargets.cmake`; a consumer
only finds BeaconQueue and links `Beacon::queue`. Defining a placeholder
`Threads::Threads` target is not dependency discovery.

The configuration and targets files are prefix-relative. Moving the whole
installation prefix must not require regeneration, and installed metadata must
not contain the source directory or the prefix used at installation time.
