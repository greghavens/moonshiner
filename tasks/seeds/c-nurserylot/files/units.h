/* Shared nursery types. */

struct bed_row {
    int slots;   /* pot positions the row can hold */
    int planted; /* positions currently occupied */
};
