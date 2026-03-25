MGR = billmgr
PLUGIN = pmbtgpix

SRC=$(shell pwd)

dist-prepare: $(DISTDIR)/paymethods/pmbtgpix
$(DISTDIR)/paymethods/pmbtgpix: $(SRC)/pmbtgpix.py
	@echo "BTGPix: paymethod module"
	@mkdir -p $(DISTDIR)/paymethods && \
		cp -f $(SRC)/pmbtgpix.py $(DISTDIR)/paymethods/pmbtgpix && \
		chmod 744 $(DISTDIR)/paymethods/pmbtgpix

dist-prepare: $(DISTDIR)/cgi/btgpixpayment
$(DISTDIR)/cgi/btgpixpayment: $(SRC)/btgpixpayment.py
	@echo "BTGPix: payment cgi"
	@mkdir -p $(DISTDIR)/cgi && \
		cp -f $(SRC)/btgpixpayment.py $(DISTDIR)/cgi/btgpixpayment && \
		chmod 744 $(DISTDIR)/cgi/btgpixpayment

dist-prepare: $(DISTDIR)/cgi/btgpixwebhook
$(DISTDIR)/cgi/btgpixwebhook: $(SRC)/btgpixwebhook.py
	@echo "BTGPix: webhook cgi"
	@mkdir -p $(DISTDIR)/cgi && \
		cp -f $(SRC)/btgpixwebhook.py $(DISTDIR)/cgi/btgpixwebhook && \
		chmod 744 $(DISTDIR)/cgi/btgpixwebhook

dist-prepare: $(DISTDIR)/cgi/btgpixauth
$(DISTDIR)/cgi/btgpixauth: $(SRC)/btgpixauth.py
	@echo "BTGPix: auth cgi"
	@mkdir -p $(DISTDIR)/cgi && \
		cp -f $(SRC)/btgpixauth.py $(DISTDIR)/cgi/btgpixauth && \
		chmod 744 $(DISTDIR)/cgi/btgpixauth

dist-prepare: $(DISTDIR)/lib/python/btgpix
$(DISTDIR)/lib/python/btgpix: $(SRC)/btgpix/*
	@echo "BTGPix: api library"
	@mkdir -p $(DISTDIR)/lib/python/btgpix && \
		cp -f $(SRC)/btgpix/* $(DISTDIR)/lib/python/btgpix/

dist-prepare: $(DISTDIR)/etc/xml/billmgr_mod_pmbtgpix.xml
$(DISTDIR)/etc/xml/billmgr_mod_pmbtgpix.xml: $(SRC)/xml/billmgr_mod_pmbtgpix.xml
	@echo "BTGPix: xml config"
	@mkdir -p $(DISTDIR)/etc/xml && \
		cp -f $(SRC)/xml/billmgr_mod_pmbtgpix.xml $(DISTDIR)/etc/xml/

dist-prepare: $(DISTDIR)/skins/common/plugin-logo/billmanager-plugin-pmbtgpix.png
$(DISTDIR)/skins/common/plugin-logo/billmanager-plugin-pmbtgpix.png: $(SRC)/dist/skins/common/plugin-logo/billmanager-plugin-pmbtgpix.png
	@echo "BTGPix: logo"
	@mkdir -p $(DISTDIR)/skins/common/plugin-logo && \
		cp -f $(SRC)/dist/skins/common/plugin-logo/billmanager-plugin-pmbtgpix.png $(DISTDIR)/skins/common/plugin-logo/

BASE ?= /usr/local/mgr5
include $(BASE)/src/isp.mk
