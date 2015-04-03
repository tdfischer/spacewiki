# SpaceWiki

All wiki software sucks. SpaceWiki sucks less than most.

I've been involved with building hackerspaces for the last ~5 years or so. Every
space has a wiki and more often than not it is MediaWiki.

For the longest time, I've wanted some sort of wiki that is tailored
specifically towards low-maintenence installs where nobody should rely on a
swarm of volunteers to curate, maintain, and otherwise write content. Wikipedia
has that and it works fantastic for them. Noisebridge does not and the wiki
tends to turn into a mess of outdated pages, broken images, and the like.
Entropy kills.

## Features

SpaceWiki has an exceptionally tiny feature set which should make it easy for
others to get up and going with minimal setup:

* Default sqlite database out of the box
* Markdown page syntax
* The familiar MediaWiki syntax for linking to other pages
* Softlinks for easy-peasy organic discovery of related pages. If you are
  unfamiliar with Softlinks, [check out how Everything2 does
  it](http://everything2.com/title/Soft+link)

## Setup

    $ ./app.py --syncdb
    $ ./app.py

## TODO

* Support more than sqlite
* Image uploads
* Crazy easy "attach photo" button for use with a mobile device/camera