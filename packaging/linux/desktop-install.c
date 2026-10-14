/* Optional own desktop entry: exclusive creation, no path-link traversal. */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>

static int failure(void) {
    fputs("Desktop entry refused or incomplete; existing files remain untouched.\n", stderr);
    return 1;
}

static int valid_path(const char *path) {
    if (!path || path[0] != '/' || strnlen(path, PATH_MAX) >= PATH_MAX)
        return 0;
    const char *segment = path + 1;
    for (const char *p = segment;; ++p) {
        if ((unsigned char)*p < 32 && *p != '\0') return 0;
        if (*p == '/' || *p == '\0') {
            size_t length = (size_t)(p - segment);
            if ((length == 1 && segment[0] == '.') ||
                (length == 2 && segment[0] == '.' && segment[1] == '.')) return 0;
            if (*p == '\0') return 1;
            segment = p + 1;
        }
    }
}

static int directory_fd(const char *path, int create) {
    if (!valid_path(path)) { errno = EINVAL; return -1; }
    char *copy = strdup(path);
    if (!copy) return -1;
    int current = open("/", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    char *state = NULL;
    for (char *part = strtok_r(copy, "/", &state); current >= 0 && part;
         part = strtok_r(NULL, "/", &state)) {
        int next = openat(current, part, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        if (next < 0 && errno == ENOENT && create) {
            if (mkdirat(current, part, 0700) == 0 || errno == EEXIST)
                next = openat(current, part, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        }
        close(current);
        current = next;
    }
    free(copy);
    return current;
}

static int write_all(int fd, const char *data, size_t size) {
    while (size) {
        ssize_t written = write(fd, data, size);
        if (written < 0 && errno == EINTR) continue;
        if (written <= 0) return -1;
        data += written;
        size -= (size_t)written;
    }
    return 0;
}

int main(int argc, char **argv) {
    (void)argv;
    if (argc != 1) return failure();
    char program[PATH_MAX + 1], start[PATH_MAX], desktop[PATH_MAX];
    ssize_t length = readlink("/proc/self/exe", program, PATH_MAX);
    if (length <= 0 || length >= PATH_MAX) return failure();
    program[length] = '\0';
    char *slash = strrchr(program, '/');
    if (!slash) return failure();
    *slash = '\0';
    if (!valid_path(program) || strchr(program, '=')) return failure();
    int own = directory_fd(program, 0);
    if (own < 0) return failure();
    int script = openat(own, "start.sh", O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
    close(own);
    struct stat info;
    if (script < 0) return failure();
    int valid_script = fstat(script, &info) == 0 && S_ISREG(info.st_mode) &&
                       syscall(SYS_faccessat2, script, "", X_OK, AT_EMPTY_PATH | AT_EACCESS) == 0;
    close(script);
    if (!valid_script) return failure();
    /* Existing env avoids GIO's raw argv0 existence check before %% expansion. */
    int system_directory = directory_fd("/usr/bin", 0);
    if (system_directory < 0) return failure();
    int launcher = openat(system_directory, "env", O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
    close(system_directory);
    if (launcher < 0) return failure();
    int valid_launcher = fstat(launcher, &info) == 0 && S_ISREG(info.st_mode) &&
                         info.st_uid == 0 && !(info.st_mode & (0022 | S_ISUID | S_ISGID)) &&
                         syscall(SYS_faccessat2, launcher, "", X_OK, AT_EMPTY_PATH | AT_EACCESS) == 0;
    close(launcher);
    if (!valid_launcher) return failure();
    if (snprintf(start, sizeof(start), "%s/start.sh", program) >= (int)sizeof(start)) return failure();

    /* String unescaping precedes Exec quoting: backslashes need four bytes. */
    char escaped[PATH_MAX * 4 + 1];
    char *out = escaped;
    for (const char *p = start; *p; ++p) {
        if (*p == '\\') { memcpy(out, "\\\\\\\\", 4); out += 4; }
        else if (*p == '"' || *p == '$' || *p == '`') {
            *out++ = '\\'; *out++ = '\\'; *out++ = *p;
        } else if (*p == '%') { *out++ = '%'; *out++ = '%'; }
        else *out++ = *p;
    }
    *out = '\0';
    const char *data = getenv("XDG_DATA_HOME");
    int count;
    if (data && *data) count = snprintf(desktop, sizeof(desktop), "%s/applications", data);
    else {
        const char *home = getenv("HOME");
        if (!home || !*home) return failure();
        count = snprintf(desktop, sizeof(desktop), "%s/.local/share/applications", home);
    }
    if (count < 0 || count >= (int)sizeof(desktop)) return failure();
    int directory = directory_fd(desktop, 1);
    if (directory < 0) return failure();
    int file = openat(directory, "studycenter-public.desktop",
                      O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
    close(directory);
    if (file < 0) return failure();
    char entry[PATH_MAX * 4 + 512];
    count = snprintf(entry, sizeof(entry),
        "[Desktop Entry]\nType=Application\nName=StudyCenter-Public\n"
        "Comment=Local study organisation\nExec=/usr/bin/env -- \"%s\"\nTerminal=false\nCategories=Education;\n", escaped);
    int success = count >= 0 && count < (int)sizeof(entry) && fchmod(file, 0600) == 0 &&
                  write_all(file, entry, (size_t)count) == 0 && fsync(file) == 0;
    if (close(file) < 0) success = 0;
    /* No pathname cleanup: another process could have replaced the directory entry. */
    if (!success) return failure();
    puts("Desktop entry installed for this user.");
    return 0;
}
