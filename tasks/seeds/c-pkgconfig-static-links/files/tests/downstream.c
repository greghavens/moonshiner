#include <parcel/parcel.h>

#include <stdio.h>

int main(void)
{
    const int score = parcel_score("box");

    if (score != 339) {
        return 1;
    }
    if (printf("%d\n", score) < 0) {
        return 2;
    }
    return 0;
}
