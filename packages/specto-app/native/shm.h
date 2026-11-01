// Named shared-memory blocks created by Python's multiprocessing.shared_memory (Windows
// file mapping or POSIX shm_open): a one-shot read of a slice, and a mapping kept open.
#pragma once

#include <QtCore/QString>
#include <cstddef>

namespace specto {

// Copies `nbytes` starting at `offset` into `dst`. Returns false when the block cannot
// be opened or is too small; `error` gets a short reason.
bool readSharedMemory(const QString &name, size_t offset, size_t nbytes, void *dst, QString *error);

// A whole block mapped read-write until close() or destruction; the mapping keeps the
// memory alive after its creator closes or unlinks it.
class SharedMemoryMap
{
public:
    SharedMemoryMap() = default;
    ~SharedMemoryMap() { close(); }
    SharedMemoryMap(const SharedMemoryMap &) = delete;
    SharedMemoryMap &operator=(const SharedMemoryMap &) = delete;

    bool open(const QString &name, QString *error);
    void close();
    char *data() const { return m_base; }
    size_t size() const { return m_size; }
    bool isOpen() const { return m_base != nullptr; }

private:
    char *m_base = nullptr;
    size_t m_size = 0;
#if defined(_WIN32)
    void *m_handle = nullptr;
#endif
};

} // namespace specto
