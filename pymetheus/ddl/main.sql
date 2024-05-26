create table item
(
    id integer
        constraint item_pkey
            primary key autoincrement,
    type text not null,
    field_data text not null,
    creators text not null
);

create table collection
(
    name text not null
        constraint collection_pkey
            primary key
);

create table collection_entry
(
    id serial not null
        constraint collection_entry_pkey
            primary key,
    collection name
        not null
        constraint collection_entry_collection_id_fkey
            references collection
            on delete cascade,
    item integer
        not null
        constraint collection_entry_entry_id_fkey
            references item
            on delete cascade
);
