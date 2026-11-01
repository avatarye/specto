#include "shm.h"

#include <cstring>

#if defined(_WIN32)
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#else
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>
#endif

namespace specto {

#if defined(_WIN32)
bool readSharedMemory(const QString &name, size_t offset, size_t nbytes, void *dst, QString *error)
{
    const std::wstring wname = name.toStdWString();
    HANDLE h = OpenFileMappingW(FILE_MAP_READ, FALSE, wname.c_str());
    if (!h) {
        if (error)
            *error = QStringLiteral("OpenFileMapping failed (%1)").arg(GetLastError());
        return false;
    }
    void *base = MapViewOfFile(h, FILE_MAP_READ, 0, 0, 0);
    if (!base) {
        if (error)
            *error = QStringLiteral("MapViewOfFile failed (%1)").arg(GetLastError());
        CloseHandle(h);
        return false;
    }
    MEMORY_BASIC_INFORMATION info{};
    bool ok = true;
    if (VirtualQuery(base, &info, sizeof info) == sizeof info && info.RegionSize < offset + nbytes) {
        ok = false;
        if (error)
            *error = QStringLiteral("mapping too small: %1 < %2").arg(info.RegionSize).arg(offset + nbytes);
    }
    if (ok)
        std::memcpy(dst, static_cast<const char *>(base) + offset, nbytes);
    UnmapViewOfFile(base);
    CloseHandle(h);
    return ok;
}
#else
bool readSharedMemory(const QString &name, size_t offset, size_t nbytes, void *dst, QString *error)
{
    QString path = name.startsWith(QLatin1Char('/')) ? name : QLatin1Char('/') + name;
    const QByteArray p = path.toLocal8Bit();
    int fd = shm_open(p.constData(), O_RDONLY, 0);
    if (fd < 0) {
        if (error)
            *error = QStringLiteral("shm_open failed (%1)").arg(errno);
        return false;
    }
    struct stat st{};
    if (fstat(fd, &st) != 0 || size_t(st.st_size) < offset + nbytes) {
        if (error)
            *error = QStringLiteral("shm too small");
        close(fd);
        return false;
    }
    void *base = mmap(nullptr, size_t(st.st_size), PROT_READ, MAP_SHARED, fd, 0);
    close(fd);
    if (base == MAP_FAILED) {
        if (error)
            *error = QStringLiteral("mmap failed (%1)").arg(errno);
        return false;
    }
    std::memcpy(dst, static_cast<const char *>(base) + offset, nbytes);
    munmap(base, size_t(st.st_size));
    return true;
}
#endif

#if defined(_WIN32)
bool SharedMemoryMap::open(const QString &name, QString *error)
{
    close();
    const std::wstring wname = name.toStdWString();
    HANDLE h = OpenFileMappingW(FILE_MAP_READ | FILE_MAP_WRITE, FALSE, wname.c_str());
    if (!h) {
        if (error)
            *error = QStringLiteral("OpenFileMapping %1 failed (%2)").arg(name).arg(GetLastError());
        return false;
    }
    void *base = MapViewOfFile(h, FILE_MAP_READ | FILE_MAP_WRITE, 0, 0, 0);
    if (!base) {
        if (error)
            *error = QStringLiteral("MapViewOfFile %1 failed (%2)").arg(name).arg(GetLastError());
        CloseHandle(h);
        return false;
    }
    MEMORY_BASIC_INFORMATION info{};
    VirtualQuery(base, &info, sizeof info);
    m_handle = h;
    m_base = static_cast<char *>(base);
    m_size = info.RegionSize;
    return true;
}

void SharedMemoryMap::close()
{
    if (m_base)
        UnmapViewOfFile(m_base);
    if (m_handle)
        CloseHandle(m_handle);
    m_base = nullptr;
    m_handle = nullptr;
    m_size = 0;
}
#else
bool SharedMemoryMap::open(const QString &name, QString *error)
{
    close();
    QString path = name.startsWith(QLatin1Char('/')) ? name : QLatin1Char('/') + name;
    const QByteArray p = path.toLocal8Bit();
    int fd = shm_open(p.constData(), O_RDWR, 0);
    if (fd < 0) {
        if (error)
            *error = QStringLiteral("shm_open %1 failed (%2)").arg(name).arg(errno);
        return false;
    }
    struct stat st{};
    if (fstat(fd, &st) != 0 || st.st_size <= 0) {
        if (error)
            *error = QStringLiteral("shm %1 is empty").arg(name);
        ::close(fd);
        return false;
    }
    void *base = mmap(nullptr, size_t(st.st_size), PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    ::close(fd);
    if (base == MAP_FAILED) {
        if (error)
            *error = QStringLiteral("mmap %1 failed (%2)").arg(name).arg(errno);
        return false;
    }
    m_base = static_cast<char *>(base);
    m_size = size_t(st.st_size);
    return true;
}

void SharedMemoryMap::close()
{
    if (m_base)
        munmap(m_base, m_size);
    m_base = nullptr;
    m_size = 0;
}
#endif

} // namespace specto
