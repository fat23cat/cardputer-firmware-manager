#pragma once

// NimBLE spam modes may deinitialize the controller between loop iterations
// while retaining their advertising and scan pointers. Touch those objects
// only while the controller is initialized, then invalidate both pointers.
template <class Device, class Advertising, class Scan, class Pause>
bool crubShutdownBle(Advertising*& advertising, Scan*& scan, Pause pause) {
    const bool initialized = Device::getInitialized();
    if (initialized) {
        if (advertising) advertising->stop();
        if (scan) {
            scan->stop();
            scan->clearResults();
        }
        pause();
        Device::deinit();
    }
    advertising = nullptr;
    scan = nullptr;
    return initialized;
}
