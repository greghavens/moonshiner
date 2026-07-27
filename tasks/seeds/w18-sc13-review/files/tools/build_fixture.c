#include <stdio.h>

static const char usage[] =
    "metadata-helper prints one label; it does not invoke a system shell";

int main(int argc, char **argv)
{
    if (argc != 2) {
        puts(usage);
        return 2;
    }
    printf("metadata-label: %s\n", argv[1]);
    return 0;
}
