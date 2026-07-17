# kilnprofiles.ps1 -- per-kiln firing profiles, dot-sourced by kilnsheet.ps1.
# soak is minutes at top temperature; shelf is the stacking layout the
# loaders expect for that kiln.
$Profiles = @{
    kilns  = @{
        electra = @{ soak = 15; shelf = 'half'
        gasA    = @{ soak = 20; shelf = 'tall' }
        gasB    = @{ soak = 35; shelf = 'tall' }
    }
    window = 'morning'
}
