/* JARVIS.app's own executable.
 *
 * It starts Python as a CHILD (not exec), so macOS treats JARVIS.app — whose
 * Info.plist explains why it needs the microphone — as the "responsible" app.
 * That's what makes the "JARVIS would like to access the microphone" prompt
 * appear, and the permission is then shared by everything JARVIS starts.
 * JARVIS_ROOT is baked in at build time by make_app.sh.
 */
#include <spawn.h>
#include <stdio.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;

int main(void) {
    const char *root = JARVIS_ROOT;
    char py[4096], app[4096];
    snprintf(py, sizeof py, "%s/.venv/bin/python", root);
    snprintf(app, sizeof app, "%s/app.py", root);
    if (chdir(root) != 0) { perror("chdir"); return 1; }
    char *argv[] = {py, "-W", "ignore", app, NULL};
    pid_t pid;
    if (posix_spawn(&pid, py, NULL, NULL, argv, environ) != 0) { perror("spawn"); return 1; }
    int st = 0;
    waitpid(pid, &st, 0);
    return WIFEXITED(st) ? WEXITSTATUS(st) : 1;
}
